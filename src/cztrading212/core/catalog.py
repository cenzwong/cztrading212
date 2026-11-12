"""Generated operation catalog shared by transport and policies."""

import json
from importlib.resources import files
from typing import Any

OPERATIONS: dict[str, dict[str, Any]] = {
    op["operation_id"]: op
    for op in json.loads(files("cztrading212._generated").joinpath("operations.json").read_text())
}
