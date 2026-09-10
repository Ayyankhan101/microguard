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
from microguard.live.server import CheckHandler

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

# socketserver.ThreadingMixIn reads self.daemon_threads in process_request;
# run_server only writes it, so vulture sees a write with no read.
_.daemon_threads

# NoRedirect.redirect_request is a nested class inside scanner.probe_url,
# so it can't be imported here — reference it via attribute access instead.
_.redirect_request
