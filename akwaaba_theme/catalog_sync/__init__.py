"""One-way catalog sync: edit products on the laptop, push them to live.

Only catalog records travel (item groups, items and variants, prices, website
items, galleries, photos). Orders, customers, payments and stock quantities never
do, so a push can't touch the books.

	pull   brings live's catalog down, so the first push doesn't revert anything
	push   shows what would change; --apply does it

Run from the bench's sites folder: ../env/bin/python -m akwaaba_theme.catalog_sync
"""
