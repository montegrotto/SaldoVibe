"""Shared plumbing for the view modules.

`company_required` is bound to a logger at import time, so it has to be created
once rather than per module — otherwise the nine modules each carry their own
copy of a line that must stay in sync.
"""

import logging

from ..company_scope import require_company

logger = logging.getLogger(__name__)

company_required = require_company(logger=logger, log_message="Active company is missing")
