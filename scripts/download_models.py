"""Prepare pinned model artifacts using the same path as the application."""
import sys
from onsei.models import prepare
for name in sys.argv[1:]:
    print(prepare(name))
