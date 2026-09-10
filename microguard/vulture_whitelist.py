# ruff: noqa
# Vulture whitelist: confirmed false positives (tested utilities / framework
# method overrides that vulture can't detect via static analysis). Not
# executed or imported by the package — parsed by vulture only, so this file
# is excluded from ruff (see file-level noqa above) and mypy (see mypy.ini).
from microguard.model import BotDetector
from microguard.parser import LogEntry
from microguard.features import _url_depth, _url_width
from microguard.training.groundtruth import GroundTruthRule, label_line
from microguard.live.middleware import MicroguardASGI, MicroguardWSGI
from microguard.live.redis_store import RedisSessionStateStore
from microguard.live.server import CheckHandler
from microguard.live.state import SessionStateStore

BotDetector.sigmoid
LogEntry.to_dict
_url_depth
_url_width
GroundTruthRule.id
GroundTruthRule.sensitivity
label_line

# Public middleware entrypoints — used from README + tests, not from the package.
MicroguardASGI
MicroguardWSGI

# BaseHTTPRequestHandler dispatch + override; called by http.server, not by us.
CheckHandler.do_GET
CheckHandler.log_message

# Scored-session API: exercised in tests/live/test_redis_store.py.
RedisSessionStateStore.incr_score
RedisSessionStateStore.get_score

# Protocol declarations — bodies are `...` by design.
SessionStateStore.incr_score
SessionStateStore.get_score

# NoRedirect.redirect_request is a nested class inside scanner.probe_url,
# so it can't be imported here — reference it via attribute access instead.
_.redirect_request
