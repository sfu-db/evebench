from .benchmarks import router as benchmarks_router
from .checkers import router as checkers_router
from .checks import router as checks_router
from .generation import router as generation_router

__all__ = [
	"benchmarks_router",
	"checkers_router",
	"checks_router",
	"generation_router",
]
