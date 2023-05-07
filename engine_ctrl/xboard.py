import shogi
import threading
import subprocess
import os
import signal
import logging

logger = logging.getLogger(__name__)


class Engine:
    def __init__(self, command, cwd=None):
        self.info = {}
        cwd = cwd or os.path.realpath(os.path.expanduser("."))
        self.proccess = self.open_process(command, cwd)
        self.go_commands = None
        self.force = False
        self.setboard = False
        self.sfen = None
        self.usermove = False

    def set_go_commands(self, go_comm):
        self.go_commands = go_comm
        logger.info(self.go_commands)

    def open_process(self, command, cwd=None, shell=True, _popen_lock=threading.Lock()):
        kwargs = {
            "shell": shell,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "stdin": subprocess.PIPE,
            "bufsize": 1,  # Line buffered
            "universal_newlines": True,
        }

        if cwd is not None:
            kwargs["cwd"] = cwd

        # Prevent signal propagation from parent process
        try:
            # Windows
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        except AttributeError:
            # Unix
            kwargs["preexec_fn"] = os.setpgrp

        with _popen_lock:  # Work around Python 2 Popen race condition
            return subprocess.Popen(command, **kwargs)

    def kill_process(self):
        try:
            # Windows
            self.proccess.send_signal(signal.CTRL_BREAK_EVENT)
        except AttributeError:
            # Unix
            os.killpg(self.proccess.pid, signal.SIGKILL)

    def send(self, line):
        logger.debug(f"<< {line}")
        assert self.proccess.stdin is not None
        self.proccess.stdin.write(line + "\n")
        self.proccess.stdin.flush()

    def recv(self):
        while True:
            assert self.proccess.stdout is not None
            line = self.proccess.stdout.readline()
            if line == "":
                raise EOFError()
            line = line.rstrip()
            logger.debug(f">> {line}")
            if line:
                return line

    def recv_xboard(self):
        command_and_args = self.recv().split(None, 1)
        if len(command_and_args) == 1:
            return command_and_args[0], ""
        elif len(command_and_args) == 2:
            return command_and_args

    def xboard(self, variant, base, inc, byo):
        self.send("xboard\nprotover 2")
        self.base = base
        self.inc = inc
        self.byo = byo
        self.send("level 0 %d %d" % (self.base, self.inc + self.byo))
        self.send("new")
        self.setboard = False
        self.usermove = False

        engine_info = {}

        while True:
            command, args = self.recv_xboard()

            if command == "feature":
                if args == "done=1":
                    return engine_info
                args = args.split(" ")

                if "setboard=1" in args:
                    self.setboard = True
                if "usermove=1" in args:
                    self.usermove = True
                pass
            elif command == "#":
                logger.info("%s %s" % (command, args))
            elif command in ["Error", "Error:"]:
                logger.error("Unexpected engine response to protover 2: %s %s" % (command, args))
            else:
                logger.warning("Unexpected engine response to protover 2: %s %s" % (command, args))

    def setoption(self, name, value):
        name = name.lower()
        if name == "hash":
            name = "memory"
        elif name == "threads":
            name = "cores"

        if value is True:
            value = "true"
        elif value is False:
            value = "false"
        elif value is None:
            value = "none"

        self.send("%s %s" % (name, value))

    def set_variant_options(self, variant):
        if variant == "chushogi":
            variant = "chu"
            self.sfen = "lfcsgekgscfl/a1b1txot1b1a/mvrhdqndhrvm/pppppppppppp/3i4i3/12/12/3I4I3/PPPPPPPPPPPP/MVRHDNQDHRVM/A1B1TOXT1B1A/LFCSGKEGSCFL b - 1"
        elif variant == "minishogi":
            variant = "minishogi"
            self.sfen = "rbsgk/4p/5/P4/KGSBR b - 1"
        else:
            variant = "shogi"
            self.sfen = "lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1"
        self.send("variant %s" % variant)
        self.force = True
        # Sjaak II does not respond with setup SFEN, therefore a loop cannot be used
        command, args = self.recv_xboard()
        if command == "setup":
            # In CECP (xboard) White moves first (e.g. 1. P-c4 / c3c4)
            # However, setboard can be used to force Black to move first (e.g. 1... P-g6 / g7g6)
            sfen = args.split(" ", 2)[-1]
            sfen = sfen.split(" ", 2)
            self.sfen = "%s b %s" % (sfen[0][::-1].swapcase(), sfen[2])
        elif command == "#":
            logger.info("%s %s" % (command, args))
        elif command in ["Error", "Error:"]:
            logger.error("Unexpected engine response to variant: %s %s" % (command, args))
        else:
            logger.warning("Unexpected engine response to variant: %s %s" % (command, args))

    def ping(self):
        self.send("ping 1")
        while line := self.recv():
            if line == "pong 1":
                return True
            else:
                logger.warning("Unexpected engine response to ping: %s" % line)
        return False

    def go(self, startpos, moves, sfen, turn, movetime=None, btime=None, wtime=None, binc=None, winc=None, byo=None, depth=None, nodes=None, ponder=False):
        time = btime if turn == shogi.BLACK else wtime
        otim = wtime if turn == shogi.BLACK else btime

        if self.force:
            self.send("hard" if ponder else "easy")
            if not self.ping():
                logger.error("Unexpected engine termination")
                return
            self.send("force")
            # Future work: if sfen is never None, deprecate self.sfen
            self.position(None, None, self.sfen)
        self.position(startpos, moves, sfen)

        builder = []
        if movetime is not None:
            builder.append("st %d" % movetime)
        if depth is not None:
            builder.append("sd %d" % depth)
        if time is not None:
            builder.append("time %d" % (time // 10))
        if otim is not None:
            builder.append("otim %d" % (otim // 10))
        self.send("\n".join(builder))

        if self.force:
            self.send("go")
            self.force = False

        info = {}
        info["move"] = None
        info["pondermove"] = None

        while True:
            command, args = self.recv_xboard()

            if command == "move":
                args_split = args.split()
                bestmove = args_split[0]
                if bestmove and bestmove != "@@@@":
                    # Translate 1... P-g6 (g7g6) -> 7g7f
                    # TODO: support handicap and other From Position games
                    files = {'a': '1', 'b': '2', 'c': '3', 'd': '4', 'e': '5', 'f': '6', 'g': '7', 'h': '8', 'i': '9'}
                    ranks = {'1': 'a', '2': 'b', '3': 'c', '4': 'd', '5': 'e', '6': 'f', '7': 'g', '8': 'h', '9': 'i'}

                    info["bestmove"] = ''
                    if bestmove[1] == '@':
                        info["bestmove"] += bestmove[0] + "*"
                    else:
                        info["bestmove"] += files[bestmove[0]]
                        info["bestmove"] += ranks[bestmove[1]]
                    info["bestmove"] += files[bestmove[2]]
                    info["bestmove"] += ranks[bestmove[3]]
                    if len(bestmove) > 4:
                        info["bestmove"] += bestmove[4:]
                #if len(args_split) == 3:
                #    if args_split[1] == "ponder":
                #        ponder_move = args_split[2]
                #        if ponder_move and ponder_move != "@@@@":
                #            info["pondermove"] = ponder_move
                if movetime is not None:
                    # restore time control for future turns
                    self.send("level 0 %d %d" % (self.base, self.inc + self.byo))
                return (info["bestmove"], info["pondermove"])

            elif command == "info":
                # Parse all other parameters
                score_kind, score_value, lowerbound, upperbound = None, None, False, False
                current_parameter = None
                for token in args.split(" "):
                    if current_parameter == "string":
                        # Everything until the end of line is a string
                        if "string" in info:
                            info["string"] += " " + token
                        else:
                            info["string"] = token
                    elif token == "score":
                        current_parameter = "score"
                    elif token == "pv":
                        current_parameter = "pv"
                        if info.get("multipv", 1) == 1:
                            info.pop("pv", None)
                    elif token in ["depth", "seldepth", "time", "nodes", "multipv",
                                "currmove", "currmovenumber",
                                "hashfull", "nps", "tbhits", "cpuload",
                                "refutation", "currline", "string"]:
                        current_parameter = token
                        info.pop(current_parameter, None)
                    elif current_parameter in ["depth", "seldepth", "time",
                                            "nodes", "currmovenumber",
                                            "hashfull", "nps", "tbhits",
                                            "cpuload", "multipv"]:
                        # Integer parameters
                        info[current_parameter] = int(token)
                    elif current_parameter == "score":
                        # Score
                        if token in ["cp", "mate"]:
                            score_kind = token
                            score_value = None
                        elif token == "lowerbound":
                            lowerbound = True
                        elif token == "upperbound":
                            upperbound = True
                        else:
                            score_value = int(token)
                    elif current_parameter != "pv" or info.get("multipv", 1) == 1:
                        # Strings
                        if current_parameter in info:
                            info[current_parameter] += " " + token
                        else:
                            info[current_parameter] = token

                # Set score. Prefer scores that are not just a bound
                if score_kind and score_value is not None and (not (lowerbound or upperbound) or "score" not in info or info["score"].get("lowerbound") or info["score"].get("upperbound")):
                    info["score"] = {score_kind: score_value}
                    if lowerbound:
                        info["score"]["lowerbound"] = lowerbound
                    if upperbound:
                        info["score"]["upperbound"] = upperbound
                self.info = info
            elif command == "#":
                logger.info("%s %s" % (command, args))
            elif command in ["Error", "Error:"]:
                logger.error("Unexpected engine response to go: %s %s" % (command, args))
            else:
                logger.warning("Unexpected engine response to go: %s %s" % (command, args))

    def position(self, startpos, moves, sfen=None):
        if moves:
            self.send(self.move(moves[-1]))
        elif self.setboard:
            # In CECP (xboard) White moves first (e.g. 1. P-c4 / c3c4)
            # However, setboard can be used to force Black to move first (e.g. 1... P-g6 / g7g6)
            # UCI/USI regard startpos as a valid value, whereas CECP/XBoard does not
            if startpos == "startpos":
                startpos = self.sfen
            if sfen == "startpos":
                sfen = self.sfen
            sfen = sfen.split(" ", 2)
            # TODO: support handicap games (switch white/black instead of board rotation?)
            position = "%s %s %s" % (sfen[0][::-1].swapcase(), sfen[1], sfen[2])
            self.send("setboard %s" % position)

    def move(self, move):
        # Translate 7g7f -> 1... P-g6 (g7g6)
        # TODO: support handicap and other From Position games
        files = {'a': '1', 'b': '2', 'c': '3', 'd': '4', 'e': '5', 'f': '6', 'g': '7', 'h': '8', 'i': '9'}
        ranks = {'1': 'a', '2': 'b', '3': 'c', '4': 'd', '5': 'e', '6': 'f', '7': 'g', '8': 'h', '9': 'i'}
        usermove = ''
        if move[1] == '*':
            usermove += move[0] + "@"
        else:
            usermove += ranks[move[0]]
            usermove += files[move[1]]
        usermove += ranks[move[2]]
        usermove += files[move[3]]
        if len(move) > 4:
            usermove += move[4]
        return ("usermove %s" % usermove) if self.usermove else usermove

    def stop(self):
        self.send("stop")

    def ponderhit(self):
        self.send("ponderhit")
        logger.info("ponderhit")

    def quit(self):
        self.send("quit")
