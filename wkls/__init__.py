import sys
from importlib.metadata import version, PackageNotFoundError

from .core import Wkl

# Create the main instance
wkls_instance = Wkl()

# Expose package version on the instance
try:
    wkls_instance.__version__ = version("wkls")
except PackageNotFoundError:
    wkls_instance.__version__ = "0.0.0.dev"

# Replace the module with the instance
sys.modules[__name__] = wkls_instance
