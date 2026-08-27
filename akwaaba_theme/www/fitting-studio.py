"""/fitting-studio — standalone preview of the Fitting Studio widget.

The widget itself is pure front-end (assets/akwaaba_theme/js/fitting-studio.js).
This controller only supplies the shared nav/footer context so the page frames
consistently with the rest of the storefront.
"""

from akwaaba_theme.context import with_shared_context


def get_context(context):
	context.no_cache = 1
	context.show_sidebar = False
	with_shared_context(context)
	context.title = "The Fitting Studio — Serwa Kollections"
	return context
