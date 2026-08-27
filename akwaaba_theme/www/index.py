from akwaaba_theme.context import get_hero_slides, with_shared_context


def get_context(context):
	context.no_cache = 1
	context.show_sidebar = False
	context.title = "Serwa Kollections"
	with_shared_context(context)
	# Convenience: expose homepage collections directly for the template
	context.collections = context.akw_homepage_collections
	context.collections_count = len(context.collections)
	# Items flagged "Feature on Hero" → cycling image/video backdrop
	context.hero_slides = get_hero_slides()
	return context
