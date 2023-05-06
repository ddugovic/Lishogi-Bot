import os
import pytest
import pytest_timeout
import requests
import time
import zipfile
import yaml
import shutil
import importlib
lishogi_bot = importlib.import_module("lishogi-bot")

TOKEN = os.environ['BOT_TOKEN']


def run_bot(CONFIG, logging_level):
    lishogi_bot.logger.info(lishogi_bot.intro())
    li = lishogi_bot.lishogi.Lishogi(CONFIG["token"], CONFIG["url"], lishogi_bot.__version__, logging_level)

    user_profile = li.get_profile()
    username = user_profile["username"]
    is_bot = user_profile.get("title") == "BOT"
    lishogi_bot.logger.info(f"Welcome BOT {username}!")

    if not is_bot:
        is_bot = lishogi_bot.upgrade_account(li)

    if is_bot:
        game_id = li.challenge_ai()["id"]
        time.sleep(2)

        @pytest.mark.timeout(300)
        def run_test():
            lishogi_bot.start(li, user_profile, CONFIG, logging_level, None, one_game_id=game_id)
            headers = {"Accept": "application/json"}
            response = requests.get(f"https://lishogi.org/game/export/{game_id}?moves=false", headers=headers)
            json = response.json()
            winner = json["winner"]
            assert "user" in json["players"][winner]

        run_test()
    else:
        lishogi_bot.logger.error(f"{username} is not a bot account. Please upgrade it to a bot account!")


def test_bot():
    logging_level = lishogi_bot.logging.INFO
    lishogi_bot.logging_configurer(logging_level, None)
    with open("./config.yml.default") as file:
        CONFIG = yaml.safe_load(file)
    CONFIG["token"] = TOKEN
    CONFIG["engine"]["dir"] = "/usr/games"
    CONFIG["engine"]["name"] = "fairy-stockfish"
    CONFIG["engine"]["protocol"] = "usi"
    CONFIG["engine"]["go_commands"] = {}
    run_bot(CONFIG, logging_level)
    CONFIG["engine"]["name"] = "sjaakii -xboard"
    CONFIG["engine"]["protocol"] = "xboard"
    run_bot(CONFIG, logging_level)


if __name__ == "__main__":
    test_bot()
