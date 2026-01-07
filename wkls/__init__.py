import sys

from .core import Wkl

# Create the main instance
wkls_instance = Wkl()

# Replace the module with the instance
sys.modules[__name__] = wkls_instance
