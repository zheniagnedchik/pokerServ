from flask import Flask, request, jsonify
import os
import json
from collections import defaultdict
import re
from flask_cors import CORS


upload_folder = 'uploads'
if not os.path.exists(upload_folder):
    os.makedirs(upload_folder)

app = Flask(__name__)
CORS(app)
UPLOAD_FOLDER = 'uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER


def parse_hand(hand_text):
    game_info = {
        "game_id": None,
        "date": None,
        "button_seat": None,
        "players": [],
        "community_cards": {
            "flop": [],
            "turn": [],
            "river": []
        },
        "actions": {
            "preflop": [],
            "flop": [],
            "turn": [],
            "river": []
        }
    }

    # Поиск game_id
    match = re.search(r'Game (\d+)', hand_text)
    if match:
        game_info["game_id"] = int(match.group(1))

    # Поиск даты
    date_match = re.search(
        r'\*\*\* (\d{2} \d{2} \d{4} \d{2}:\d{2}:\d{2})', hand_text)
    if date_match:
        game_info["date"] = date_match.group(1)

    # Поиск кнопки
    button_match = re.search(r'Seat (\d+) is the button', hand_text)
    if button_match:
        game_info["button_seat"] = int(button_match.group(1))

    # Парсинг игроков и действий
    player_matches = re.findall(
        r'Seat (\d+): ([^()]+) \( ([\d,]+) \)', hand_text)
    for seat, name, chips in player_matches:
        player_info = {
            "seat": int(seat),
            "name": name.strip(),
            "chips": int(chips.replace(",", "")),
            "actions": {
                "preflop": [],
                "flop": [],
                "turn": [],
                "river": []
            },
            "cards": []
        }
        action_matches = re.findall(
            r'{}\s+(.*)'.format(re.escape(name)), hand_text)
        for action in action_matches:
            if "** Dealing down cards **" in action:
                card_match = re.search(
                    r'Dealt to {} \[ (..), (..) \]'.format(re.escape(name)), hand_text)
                if card_match:
                    player_info["cards"] = [
                        card_match.group(1), card_match.group(2)]
            else:
                player_info["actions"]["preflop"].append(action)
        game_info["players"].append(player_info)

    stages = ['preflop', 'flop', 'turn', 'river']
    current_stage = 'preflop'

    # Парсинг действий на различных стадиях
    lines = hand_text.split('\n')
    for line in lines:
        if "** Dealing down cards **" in line:
            current_stage = 'preflop'
        elif "** Dealing flop **" in line:
            current_stage = 'flop'
        elif "** Dealing turn **" in line:
            current_stage = 'turn'
        elif "** Dealing river **" in line:
            current_stage = 'river'
        elif any(player["name"] in line for player in game_info["players"]) and not line.strip().startswith("Seat"):
            game_info["actions"][current_stage].append(line.strip())
    return game_info


def parse_hands(text, positions_by_count):
    games_text = re.split(r'(?=Game \d+)', text)
    games = []
    for game_text in games_text:
        if game_text.strip():
            game_info = parse_hand(game_text)
            assign_positions(game_info, positions_by_count)
            games.append(game_info)
    return games


def assign_positions(game_info, positions_by_count):
    num_players = len(game_info["players"])
    positions = positions_by_count.get(num_players)
    if not positions:
        print(f"Warning: Positions not defined for {num_players} players.")
        return

    bb_index = None
    for i, player in enumerate(game_info["players"]):
        if any("big blind" in action for action in player["actions"]["preflop"]):
            bb_index = i
            break

    if bb_index is None:
        print(
            f"Warning: Big blind player not found in game {game_info['game_id']}.")
        return

    btn_index = (bb_index - 2) % num_players

    for i in range(num_players):
        position_index = (btn_index + i) % num_players
        game_info["players"][position_index]["position"] = positions[i]

    game_info["number_of_players"] = len(game_info["players"])

    return game_info


def calculate_raise_frequencies(hands, positions_group, max_bb=40, min_bb=0, min_bet_bb=0, max_bet_bb=5, min_seat=7, max_seat=9):
    frequencies = {}

    for position, desired_positions in positions_group.items():
        raise_opportunity_count = 0
        raise_count = 0

        for hand in hands:
            players = hand.get('players')
            if not players or len(players) < min_seat or len(players) > max_seat:
                continue  # Skip hands that don't have player information or don't have 7 to 9 players

            actions_preflop = hand.get('actions', {}).get('preflop', [])
            big_blind_value = 0
            for action in actions_preflop:
                if 'posts big blind' in action:
                    big_blind_value = int(action.split(
                        '[')[-1].rstrip(']').replace(',', ''))
                    break

            if big_blind_value == 0:
                continue  # Skip hands that don't have big blind information

            for desired_position in desired_positions:
                position_player = None
                player_chips = 0
                for player_info in players:
                    if player_info.get('position') == desired_position:
                        position_player = player_info['name']
                        player_chips = player_info.get('chips', 0)
                        break

                stack_size_in_bb = player_chips / big_blind_value
                if position_player is None or stack_size_in_bb > max_bb or stack_size_in_bb < min_bb:
                    continue  # Skip if player at the desired position is not found or stack size is out of bounds

                player_action_found = False
                player_action = None
                player_all_in = False
                for action in actions_preflop:
                    if f'Dealt to {position_player}' in action:
                        player_action_found = True
                        continue
                    if player_action_found:
                        if position_player in action:
                            player_action = action
                            if 'all-in' in action:
                                player_all_in = True
                            break

                if player_action and not player_all_in:
                    actions_before_player = actions_preflop[:actions_preflop.index(
                        player_action)]
                    if all(not 'calls' in action and not 'raises' in action and not 'all-in' in action for action in actions_before_player):
                        raise_opportunity_count += 1

                        if 'raises' in player_action:
                            bet_size = int(player_action.split(
                                '[')[-1].rstrip(']').replace(',', ''))
                            bet_size_in_bb = bet_size / big_blind_value
                            if min_bet_bb <= bet_size_in_bb <= max_bet_bb:
                                raise_count += 1

        raise_frequency = 0
        if raise_opportunity_count > 0:
            raise_frequency = (raise_count / raise_opportunity_count) * 100

        frequencies[position] = raise_frequency

    return json.dumps(frequencies)


# def calculate_raise_frequencies_all_inn(hands, positions_group, max_bb=40, min_bb=0, min_bet_bb=0, max_bet_bb=5, min_seat=7, max_seat=9):
#     frequencies = {}

#     for position, desired_positions in positions_group.items():
#         raise_opportunity_count = 0
#         allin_raise_count = 0

#         for hand in hands:
#             players = hand.get('players')
#             if not players or len(players) < min_seat or len(players) > max_seat:
#                 continue  # Skip hands that don't have player information or don't have 7 to 9 players

#             actions_preflop = hand.get('actions', {}).get('preflop', [])
#             big_blind_value = 0
#             for action in actions_preflop:
#                 if 'posts big blind' in action:
#                     big_blind_value = int(action.split(
#                         '[')[-1].rstrip(']').replace(',', ''))
#                     break

#             if big_blind_value == 0:
#                 continue  # Skip hands that don't have big blind information

#             for desired_position in desired_positions:
#                 position_player = None
#                 player_chips = 0
#                 player_ante = 0
#                 for player_info in players:
#                     if player_info.get('position') == desired_position:
#                         position_player = player_info['name']
#                         player_chips = player_info.get('chips', 0)

#                         # Extracting ante from player's actions
#                         for action in player_info.get('actions', {}).get('preflop', []):
#                             if 'posts ante' in action:
#                                 player_ante = int(action.split(
#                                     '[')[-1].rstrip(']').replace(',', ''))
#                                 break
#                         break

#                 stack_size_in_bb = (
#                     player_chips - player_ante) / big_blind_value
#                 if position_player is None or stack_size_in_bb > max_bb or stack_size_in_bb < min_bb:
#                     continue  # Skip if player at the desired position is not found or stack size is out of bounds

#                 player_action_found = False
#                 player_action = None
#                 for action in actions_preflop:
#                     if f'Dealt to {position_player}' in action:
#                         player_action_found = True
#                         continue
#                     if player_action_found:
#                         if position_player in action:
#                             player_action = action
#                             break

#                 if player_action:
#                     actions_before_player = actions_preflop[:actions_preflop.index(
#                         player_action)]
#                     if all(not 'calls' in action and not 'raises' in action for action in actions_before_player):
#                         raise_opportunity_count += 1

#                         if 'raises' in player_action:
#                             bet_size = int(player_action.split(
#                                 '[')[-1].rstrip(']').replace(',', ''))
#                             bet_size_in_bb = bet_size / big_blind_value
#                             if min_bet_bb <= bet_size_in_bb <= max_bet_bb and bet_size >= (player_chips - player_ante):
#                                 allin_raise_count += 1

#         allin_raise_frequency = 0
#         if raise_opportunity_count > 0:
#             allin_raise_frequency = (
#                 allin_raise_count / raise_opportunity_count) * 100

#         frequencies[position] = allin_raise_frequency

#     return json.dumps(frequencies)


def calculate_raise_frequencies_for_player(hands, positions_group, player_name, max_bb=40, min_bb=0, min_bet_bb=0, max_bet_bb=5, min_seat=7, max_seat=9):
    frequencies = {}

    for position, desired_positions in positions_group.items():
        raise_opportunity_count = 0
        allin_raise_count = 0

        for hand in hands:
            players = hand.get('players')
            if not players or len(players) < min_seat or len(players) > max_seat:
                continue  # Skip hands that don't have player information or don't have 7 to 9 players

            actions_preflop = hand.get('actions', {}).get('preflop', [])
            big_blind_value = 0
            for action in actions_preflop:
                if 'posts big blind' in action:
                    big_blind_value = int(action.split(
                        '[')[-1].rstrip(']').replace(',', ''))
                    break

            if big_blind_value == 0:
                continue  # Skip hands that don't have big blind information

            player_position = None
            player_chips = 0
            player_ante = 0
            for player_info in players:
                if player_info.get('name') == player_name:
                    player_position = player_info.get('position')
                    player_chips = player_info.get('chips', 0)
                    for action in player_info.get('actions', {}).get('preflop', []):
                        if 'posts ante' in action:
                            player_ante = int(action.split(
                                '[')[-1].rstrip(']').replace(',', ''))
                            break
                    break

            if player_position not in desired_positions:
                continue

            stack_size_in_bb = (player_chips - player_ante) / big_blind_value
            if stack_size_in_bb > max_bb or stack_size_in_bb < min_bb:
                continue  # Skip if stack size is out of bounds

            player_action_found = False
            player_action = None
            for action in actions_preflop:
                if f'Dealt to {player_name}' in action:
                    player_action_found = True
                    continue
                if player_action_found:
                    if player_name in action:
                        player_action = action
                        break

            if player_action:
                actions_before_player = actions_preflop[:actions_preflop.index(
                    player_action)]
                if all(not 'calls' in action and not 'raises' in action for action in actions_before_player):
                    raise_opportunity_count += 1

                if 'raises' in player_action:
                    bet_size = int(player_action.split(
                        '[')[-1].rstrip(']').replace(',', ''))
                    bet_size_in_bb = bet_size / big_blind_value
                    if min_bet_bb <= bet_size_in_bb <= max_bet_bb and bet_size >= (player_chips - player_ante):
                        allin_raise_count += 1

        allin_raise_frequency = 0
        if raise_opportunity_count > 0:
            allin_raise_frequency = (
                allin_raise_count / raise_opportunity_count) * 100

        frequencies[position] = allin_raise_frequency

    return json.dumps(frequencies)


def calculate_raise_frequencies(hands, positions_group, player_name, max_bb=40, min_bb=0, min_bet_bb=0, max_bet_bb=5, min_seat=7, max_seat=9):
    frequencies = {}

    for position, desired_positions in positions_group.items():
        raise_opportunity_count = 0
        raise_count = 0

        for hand in hands:
            players = hand.get('players')
            if not players or len(players) < min_seat or len(players) > max_seat:
                continue  # Skip hands that don't have player information or don't have 7 to 9 players

            actions_preflop = hand.get('actions', {}).get('preflop', [])
            big_blind_value = 0
            for action in actions_preflop:
                if 'posts big blind' in action:
                    big_blind_value = int(action.split(
                        '[')[-1].rstrip(']').replace(',', ''))
                    break

            if big_blind_value == 0:
                continue  # Skip hands that don't have big blind information

            for desired_position in desired_positions:
                position_player = None
                player_chips = 0
                for player_info in players:
                    if player_info.get('position') == desired_position and player_info.get('name') == player_name:
                        position_player = player_info['name']
                        player_chips = player_info.get('chips', 0)
                        break

                stack_size_in_bb = player_chips / big_blind_value
                if position_player is None or stack_size_in_bb > max_bb or stack_size_in_bb < min_bb:
                    continue  # Skip if player at the desired position is not found or stack size is out of bounds

                player_action_found = False
                player_action = None
                player_all_in = False
                for action in actions_preflop:
                    if f'Dealt to {position_player}' in action:
                        player_action_found = True
                        continue
                    if player_action_found:
                        if position_player in action:
                            player_action = action
                            if 'all-in' in action:
                                player_all_in = True
                            break

                if player_action and not player_all_in:
                    actions_before_player = actions_preflop[:actions_preflop.index(
                        player_action)]
                    if all(not 'calls' in action and not 'raises' in action and not 'all-in' in action for action in actions_before_player):
                        raise_opportunity_count += 1

                        if 'raises' in player_action:
                            bet_size = int(player_action.split(
                                '[')[-1].rstrip(']').replace(',', ''))
                            bet_size_in_bb = bet_size / big_blind_value
                            if min_bet_bb <= bet_size_in_bb <= max_bet_bb:
                                raise_count += 1

        raise_frequency = 0
        if raise_opportunity_count > 0:
            raise_frequency = (raise_count / raise_opportunity_count) * 100

        frequencies[position] = raise_frequency

    return json.dumps(frequencies)


positions_by_count = {
    2: ["BTN", "BB"],
    3: ["BTN", "SB", "BB"],
    4: ["BTN", "SB", "BB", "MP"],
    5: ["BTN", "SB", "BB", "MP+1", "CO"],
    6: ["BTN", "SB", "BB", "UTG+1", "MP+1", "CO",],
    7: ["BTN", "SB", "BB", "MP+1", "LJ", "HJ", "CO",],
    8: ["BTN", "SB", "BB", "UTG+1", "MP+1", "LJ", "HJ", "CO",],
    9: ["BTN", "SB", "BB", "UTG+1", "UTG+2", "MP+1", "LJ", "HJ", "CO",],
    # 10: ["BTN", "SB", "BB", "MP", "MP+1", "MP+2", "UTG", "UTG+1", "UTG+2", "CO"],
}


@app.route('/rfi_6_9', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify(error='No file part'), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify(error='No selected file'), 400

    filename = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
    file.save(filename)

    with open(filename, 'r', encoding="utf-8") as f:
        text = f.read()

    parsed_games = parse_hands(text, positions_by_count)

    positions_group = {
        'EP': ('UTG+1', 'UTG+2'),
        'MP': ('MP+1', 'LJ'),
        'HJ': ('HJ',),
        'CO': ('CO',),
        'BTN': ('BTN',),
        'SB': ('SB',),
        # 'BB': ('BB')
    }

    params = request.form.get('params')
    player_name = request.form.get('player_name')
    if not params:
        return jsonify(error='No parameters provided'), 400
    try:
        params_list = json.loads(params)  # Теперь это список параметров
    except json.JSONDecodeError:
        return jsonify(error='Invalid parameters format'), 400

    results = []
    for params in params_list:  # Итерация по списку параметров
        for param in params['value']:
            max_bb = param.get('max_bb')
            min_bb = param.get('min_bb')
            min_bet_bb = param.get('min_bet_bb')
            max_bet_bb = param.get('max_bet_bb')
            min_seat = param.get('min_seat')
            max_seat = param.get('max_seat')
            title = param.get('title')

            if any(param is None for param in [max_bb, min_bb, min_bet_bb, max_bet_bb, title, min_seat, max_seat, player_name]):
                return jsonify(error='Missing one or more parameters'), 400

            frequencies_json = calculate_raise_frequencies(
                parsed_games, positions_group, player_name, max_bb, min_bb, min_bet_bb, max_bet_bb, min_seat, max_seat,
            )

            result = json.loads(frequencies_json)
            result['title'] = title
            result['category'] = params['title']
            result['title_eader'] = params['titleHeader']
            result['table_title'] = params['table_title']
            results.append(result)

    return jsonify(data=results)


@app.route('/')
def hello_world():
    return 'Hello, World!'


# @app.route('/allin_6_9', methods=['POST'])
# def upload_files():
#     if 'file' not in request.files:
#         return jsonify(error='No file part'), 400
#     file = request.files['file']
#     if file.filename == '':
#         return jsonify(error='No selected file'), 400

#     filename = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
#     file.save(filename)

#     with open(filename, 'r', encoding="utf-8") as f:
#         text = f.read()

#     parsed_games = parse_hands(text, positions_by_count)

#     positions_group = {
#         'EP': ('UTG+1', 'UTG+2'),
#         'MP': ('MP+1', 'LJ'),
#         'HJ': ('HJ',),
#         'CO': ('CO',),
#         'BTN': ('BTN',),
#         'SB': ('SB',),
#         # 'BB': ('BB')
#     }

#     params = request.form.get('params')
#     player_name = request.form.get('player_name')
#     if not params:
#         return jsonify(error='No parameters provided'), 400
#     try:
#         params_list = json.loads(params)  # Теперь это список параметров
#     except json.JSONDecodeError:
#         return jsonify(error='Invalid parameters format'), 400

#     results = []
#     for params in params_list:  # Итерация по списку параметров
#         for param in params['value']:
#             max_bb = param.get('max_bb')
#             min_bb = param.get('min_bb')
#             min_bet_bb = param.get('min_bet_bb')
#             max_bet_bb = param.get('max_bet_bb')
#             min_seat = param.get('min_seat')
#             max_seat = param.get('max_seat')
#             title = param.get('title')

#             if any(param is None for param in [max_bb, min_bb, min_bet_bb, max_bet_bb, title, min_seat, max_seat, player_name]):
#                 return jsonify(error='Missing one or more parameters'), 400

#             frequencies_json = calculate_raise_frequencies(
#                 parsed_games, positions_group, player_name, max_bb, min_bb, min_bet_bb, max_bet_bb, min_seat, max_seat,
#             )

#             result = json.loads(frequencies_json)
#             result['title'] = title
#             result['category'] = params['title']
#             result['title_eader'] = params['titleHeader']
#             result['table_title'] = params['table_title']
#             results.append(result)

#     return jsonify(data=results)


def process_upload(calc_func):
    if 'file' not in request.files:
        return jsonify(error='No file part'), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify(error='No selected file'), 400

    filename = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
    file.save(filename)

    with open(filename, 'r', encoding="utf-8") as f:
        text = f.read()

    parsed_games = parse_hands(text, positions_by_count)

    positions_group = {
        'EP': ('UTG+1', 'UTG+2'),
        'MP': ('MP+1', 'LJ'),
        'HJ': ('HJ',),
        'CO': ('CO',),
        'BTN': ('BTN',),
        'SB': ('SB',),

    }

    params = request.form.get('params')
    player_name = request.form.get('player_name')
    if not params:
        return jsonify(error='No parameters provided'), 400
    try:
        params_list = json.loads(params)  # Теперь это список параметров
    except json.JSONDecodeError:
        return jsonify(error='Invalid parameters format'), 400

    results = []
    for params in params_list:  # Итерация по списку параметров
        for param in params['value']:
            max_bb = param.get('max_bb')
            min_bb = param.get('min_bb')
            min_bet_bb = param.get('min_bet_bb')
            max_bet_bb = param.get('max_bet_bb')
            min_seat = param.get('min_seat')
            max_seat = param.get('max_seat')
            title = param.get('title')

            if any(param is None for param in [max_bb, min_bb, min_bet_bb, max_bet_bb, title, min_seat, max_seat, player_name]):
                return jsonify(error='Missing one or more parameters'), 400

            frequencies_json = calc_func(
                parsed_games, positions_group, player_name, max_bb, min_bb, min_bet_bb, max_bet_bb, min_seat, max_seat,
            )

            result = json.loads(frequencies_json)
            result['title'] = title
            result['category'] = params['title']
            result['title_eader'] = params['titleHeader']
            result['table_title'] = params['table_title']
            results.append(result)

    return jsonify(data=results)


@app.route('/allin_6_9', methods=['POST'])
def allInn():
    return process_upload(calculate_raise_frequencies_for_player)


if __name__ == '__main__':
    app.run(debug=True)
