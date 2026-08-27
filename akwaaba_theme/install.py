"""Schema + scripts installer for Akwaaba Theme.

This app is *structural only* — it installs custom fields, notifications,
client scripts, and one server script. It does NOT seed catalog data
(Item Groups, Items, prices, accounts, Website/Webshop Settings). Those
live in the DB, get backed up, and are restored between environments.

Idempotent: safe to re-run via
    bench --site <site> execute akwaaba_theme.install.after_install
"""

import frappe


def after_install():
	print("Installing Akwaaba Theme schema…")
	_create_custom_fields()
	_ensure_ecommerce_notifications()
	_ensure_hover_video_client_script()
	_ensure_reverse_order_server_script()
	_ensure_reverse_order_client_script()
	_ensure_momo_confirm_client_script()
	seed_category_redirects()
	frappe.db.commit()
	print("Done.")


# ---------------------------------------------------------------------------
# Custom fields
# ---------------------------------------------------------------------------

def _create_custom_fields():
	"""Custom fields on Website Item (media) and Item Group (display).

	Also installs cross-version compat shims for ERPNext develop against
	Frappe v16. All storefront fields are also exported via hooks.fixtures
	so `bench migrate` on prod picks them up without re-running install.
	"""
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	create_custom_fields({
		"Sales Order": [
			# Direct-MoMo (manual) payment tracking. The storefront flow moves
			# an order Unpaid → Awaiting Confirmation → Paid; admins confirm the
			# MoMo receipt via the "Confirm MoMo Payment" button. See momo.py.
			{
				"fieldname": "akwaaba_momo_section",
				"label": "Akwaaba MoMo Payment",
				"fieldtype": "Section Break",
				"insert_after": "status",
				"collapsible": 1,
			},
			{
				"fieldname": "payment_status",
				"label": "Payment Status",
				"fieldtype": "Select",
				"options": "\nUnpaid\nAwaiting Confirmation\nPaid",
				"default": "Unpaid",
				"insert_after": "akwaaba_momo_section",
				"in_list_view": 1,
				"in_standard_filter": 1,
				"description": (
					"Unpaid → customer hasn't paid. Awaiting Confirmation → "
					"customer claims they've sent MoMo; admin must verify the "
					"receipt. Paid → confirmed (accounting cycle has run)."
				),
			},
			{
				"fieldname": "momo_payer_phone",
				"label": "MoMo Paid-From Number",
				"fieldtype": "Data",
				"insert_after": "payment_status",
				"description": "The number the customer says they paid from.",
			},
			{
				"fieldname": "momo_txn_id",
				"label": "MoMo Transaction ID",
				"fieldtype": "Data",
				"insert_after": "momo_payer_phone",
				"description": "MoMo transaction id from the customer's SMS receipt.",
			},
		],
		"Contact": [
			# Compat: ERPNext develop queries Contact.is_billing_contact but
			# Frappe v16 hasn't shipped it. Add here so party.py joins don't throw.
			{
				"fieldname": "is_billing_contact",
				"label": "Is Billing Contact",
				"fieldtype": "Check",
				"insert_after": "is_primary_contact",
				"default": "0",
				"description": (
					"Compat shim for ERPNext develop; leave 0 unless you want "
					"this contact to appear before others in party defaults."
				),
			},
		],
		"Address": [
			{
				"fieldname": "is_your_company_address",
				"label": "Is Your Company Address",
				"fieldtype": "Check",
				"insert_after": "address_type",
				"default": "0",
			},
			{
				"fieldname": "tax_category",
				"label": "Tax Category",
				"fieldtype": "Link",
				"options": "Tax Category",
				"insert_after": "is_your_company_address",
			},
		],
		"Website Item": [
			{
				"fieldname": "akwaaba_media_section",
				"label": "Akwaaba Media",
				"fieldtype": "Section Break",
				"insert_after": "website_image",
				"collapsible": 1,
			},
			{
				"fieldname": "hover_video_file",
				"label": "Hover Video (upload)",
				"fieldtype": "Attach",
				"insert_after": "akwaaba_media_section",
				"description": (
					"Drop in a short MP4 (or WebM) that plays on hover (desktop) "
					"or when the card enters the viewport (mobile). Ideal: 3-8s, "
					"3:4 aspect ratio, silent, ≤ 8MB. The URL below is filled in "
					"automatically when you upload here."
				),
			},
			{
				"fieldname": "hover_video_url",
				"label": "Hover Video URL",
				"fieldtype": "Small Text",
				"insert_after": "hover_video_file",
				"description": (
					"Auto-populated when you upload a file above. You can also "
					"paste any external URL (Cloudinary, Mux, S3) — it takes "
					"precedence over the uploaded file."
				),
			},
			{
				"fieldname": "hover_video_poster_url",
				"label": "Hover Video Poster URL",
				"fieldtype": "Small Text",
				"insert_after": "hover_video_url",
				"description": "Poster image. Defaults to Website Image if empty.",
			},
			{
				"fieldname": "feature_on_hero",
				"label": "Feature on Hero",
				"fieldtype": "Check",
				"insert_after": "hover_video_poster_url",
				"default": "0",
				"description": (
					"Show this item's image (and its hover video, if set) in "
					"the cycling backdrop on the home hero. Needs a Website "
					"Image. Unchecked = excluded from the hero."
				),
			},
			{
				"fieldname": "hero_position",
				"label": "Hero Position",
				"fieldtype": "Int",
				"insert_after": "feature_on_hero",
				"depends_on": "eval:doc.feature_on_hero",
				"description": (
					"Order in the hero rotation — lower shows first. Ties fall "
					"back to newest. Only used when Feature on Hero is checked."
				),
			},
		],
		"Item Group": [
			{
				"fieldname": "akwaaba_display_section",
				"label": "Akwaaba Display",
				"fieldtype": "Section Break",
				"insert_after": "route",
				"collapsible": 1,
			},
			{
				"fieldname": "homepage_position",
				"label": "Homepage Position",
				"fieldtype": "Int",
				"insert_after": "akwaaba_display_section",
				"description": (
					"Sort order for the homepage collections grid. "
					"0 = hide from homepage."
				),
			},
			{
				"fieldname": "homepage_eyebrow",
				"label": "Homepage Eyebrow",
				"fieldtype": "Data",
				"insert_after": "homepage_position",
				"description": (
					"Small label above the category name on the homepage card, "
					"e.g. '01 · Bonwire'."
				),
			},
			{
				"fieldname": "homepage_caption",
				"label": "Homepage Caption",
				"fieldtype": "Data",
				"insert_after": "homepage_eyebrow",
				"description": (
					"Italic tagline shown under the category name, e.g. "
					"'Adweneasa — full of ideas'."
				),
			},
			{
				"fieldname": "homepage_background_class",
				"label": "Homepage Background CSS Class",
				"fieldtype": "Data",
				"insert_after": "homepage_caption",
				"description": (
					"One of: pattern-kente, pattern-adinkra, pattern-fugu, "
					"pattern-ankara, pattern-kaba, pattern-accessories. "
					"See akwaaba.css for available presets."
				),
			},
			{
				"fieldname": "show_in_nav",
				"label": "Show in Top Navigation",
				"fieldtype": "Check",
				"insert_after": "homepage_background_class",
				"default": "1",
			},
		],
	})


# ---------------------------------------------------------------------------
# E-commerce email notifications
# ---------------------------------------------------------------------------
#
# Native Frappe Notifications on Sales Order / Sales Invoice / Delivery Note
# lifecycle events. Idempotent — reruns keep subject/message aligned with the
# app source. Admin can add/remove recipients in the Notification doc.
#
# Recipients pattern:
#   receiver_by_role: "System Manager"        → admin(s)
#   receiver_by_document_field: "contact_email" → customer (populated at checkout)
# ---------------------------------------------------------------------------

_BRAND_STYLES = (
	'font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;color:#1F1B16;'
)

_TABLE_ITEMS_BLOCK = """
  <table style="border-collapse:collapse;width:100%;max-width:520px;margin:12px 0;">
    <thead>
      <tr style="background:#F7F1E7;">
        <th style="padding:6px 8px;text-align:left;">Item</th>
        <th style="padding:6px 8px;text-align:right;">Qty</th>
        <th style="padding:6px 8px;text-align:right;">Amount</th>
      </tr>
    </thead>
    <tbody>
      {%- for it in doc.items -%}
      <tr>
        <td style="padding:6px 8px;border-bottom:1px solid #eee;">{{ it.item_name or it.item_code }}</td>
        <td style="padding:6px 8px;border-bottom:1px solid #eee;text-align:right;">×{{ it.qty|int }}</td>
        <td style="padding:6px 8px;border-bottom:1px solid #eee;text-align:right;">{{ doc.currency }} {{ '{:,.0f}'.format(it.amount or 0) }}</td>
      </tr>
      {%- endfor -%}
    </tbody>
  </table>
  <p style="font-size:1.1em;"><strong>Total: {{ doc.currency }} {{ '{:,.0f}'.format(doc.grand_total or doc.total or 0) }}</strong></p>
""".strip()


_ADMIN_NEW_ORDER_MESSAGE = f"""
<div style="{_BRAND_STYLES}">
  <h2 style="font-family:Georgia,serif;">New Akwaaba order</h2>
  <p><strong>Order:</strong> {{{{ doc.name }}}}</p>
  <p><strong>Customer:</strong> {{{{ frappe.db.get_value("Customer", doc.customer, "customer_name") or "—" }}}}<br>
     <strong>Phone:</strong> {{{{ doc.contact_mobile or "—" }}}}<br>
     <strong>Email:</strong> {{{{ doc.contact_email or "—" }}}}</p>
  {{%- set addr = doc.shipping_address_name or doc.customer_address -%}}
  {{%- if addr -%}}
    {{%- set a = frappe.get_doc("Address", addr) -%}}
    <p><strong>Deliver to:</strong><br>
      {{{{ a.address_line1 }}}}{{% if a.address_line2 %}}<br>{{{{ a.address_line2 }}}}{{% endif %}}<br>
      {{{{ a.city }}}}{{% if a.state %}}, {{{{ a.state }}}}{{% endif %}}<br>
      {{{{ a.country }}}}</p>
  {{%- endif -%}}
{_TABLE_ITEMS_BLOCK}
  {{% if doc.terms %}}<p><strong>Notes:</strong> {{{{ doc.terms }}}}</p>{{% endif %}}
  <p><a href="{{{{ frappe.utils.get_url() }}}}/app/sales-order/{{{{ doc.name }}}}">Open in ERPNext →</a></p>
</div>
""".strip()


_CUSTOMER_ORDER_PLACED_MESSAGE = f"""
<div style="{_BRAND_STYLES}">
  <h2 style="font-family:Georgia,serif;">Akwaaba — we've got your order</h2>
  <p>Medaase{{% if doc.customer %}}, {{{{ frappe.db.get_value("Customer", doc.customer, "customer_name") or "" }}}}{{% endif %}}. Your order <strong>{{{{ doc.name }}}}</strong> is in.</p>
  <p>We'll confirm payment shortly and start preparing your pieces. Any questions, just reply to this email.</p>
{_TABLE_ITEMS_BLOCK}
  <p><a href="{{{{ frappe.utils.get_url() }}}}/order/{{{{ doc.name }}}}">View your order →</a></p>
</div>
""".strip()


_CUSTOMER_PAYMENT_RECEIVED_MESSAGE = f"""
<div style="{_BRAND_STYLES}">
  <h2 style="font-family:Georgia,serif;">Payment received — thank you</h2>
  <p>We've received your payment for order <strong>{{{{ doc.name }}}}</strong>.</p>
  <p>Your order is now confirmed and moves into preparation. We'll email again when it's on its way.</p>
{_TABLE_ITEMS_BLOCK}
  <p><a href="{{{{ frappe.utils.get_url() }}}}/order/{{{{ doc.name }}}}">View your order →</a></p>
</div>
""".strip()


_CUSTOMER_ORDER_DISPATCHED_MESSAGE = f"""
<div style="{_BRAND_STYLES}">
  <h2 style="font-family:Georgia,serif;">Your Akwaaba order is on its way</h2>
  <p>Delivery note <strong>{{{{ doc.name }}}}</strong> has been dispatched.</p>
  {{% if doc.lr_no %}}<p><strong>Tracking / LR #:</strong> {{{{ doc.lr_no }}}}{{% if doc.transporter_name %}} — {{{{ doc.transporter_name }}}}{{% endif %}}</p>{{% endif %}}
  {{%- set addr = doc.shipping_address_name -%}}
  {{%- if addr -%}}
    {{%- set a = frappe.get_doc("Address", addr) -%}}
    <p><strong>Delivering to:</strong><br>
      {{{{ a.address_line1 }}}}{{% if a.address_line2 %}}<br>{{{{ a.address_line2 }}}}{{% endif %}}<br>
      {{{{ a.city }}}}{{% if a.state %}}, {{{{ a.state }}}}{{% endif %}}<br>
      {{{{ a.country }}}}</p>
  {{%- endif -%}}
  <p>Reply to this email if you need to reach us.</p>
</div>
""".strip()


_CUSTOMER_ORDER_CANCELLED_MESSAGE = f"""
<div style="{_BRAND_STYLES}">
  <h2 style="font-family:Georgia,serif;">Order {{{{ doc.name }}}} has been cancelled</h2>
  <p>Your Akwaaba order has been cancelled. If a payment was received, a refund is being processed to the same account.</p>
  <p>If you weren't expecting this, please reply — we'll sort it out.</p>
</div>
""".strip()


_ECOMMERCE_NOTIFICATIONS = [
	{
		"name": "Akwaaba New Order Email",
		"subject": "🧵 New Akwaaba order · {{ doc.name }}",
		"document_type": "Sales Order",
		"event": "New",
		"channel": "Email",
		"send_system_notification": 1,
		"recipients": [{"receiver_by_role": "System Manager"}],
		"message": _ADMIN_NEW_ORDER_MESSAGE,
	},
	{
		"name": "Akwaaba Order Placed (Customer)",
		"subject": "We've got your order · {{ doc.name }}",
		"document_type": "Sales Order",
		"event": "New",
		"channel": "Email",
		"condition": "doc.contact_email",
		"recipients": [{"receiver_by_document_field": "contact_email"}],
		"message": _CUSTOMER_ORDER_PLACED_MESSAGE,
	},
	{
		"name": "Akwaaba Payment Received (Customer)",
		"subject": "Payment received · {{ doc.name }}",
		"document_type": "Sales Order",
		"event": "Submit",
		"channel": "Email",
		"condition": "doc.contact_email",
		"recipients": [{"receiver_by_document_field": "contact_email"}],
		"message": _CUSTOMER_PAYMENT_RECEIVED_MESSAGE,
	},
	{
		"name": "Akwaaba Order Dispatched (Customer)",
		"subject": "Your order is on its way · {{ doc.name }}",
		"document_type": "Delivery Note",
		"event": "Submit",
		"channel": "Email",
		"condition": "doc.contact_email",
		"recipients": [{"receiver_by_document_field": "contact_email"}],
		"message": _CUSTOMER_ORDER_DISPATCHED_MESSAGE,
	},
	{
		"name": "Akwaaba Order Cancelled (Customer)",
		"subject": "Your order has been cancelled · {{ doc.name }}",
		"document_type": "Sales Order",
		"event": "Cancel",
		"channel": "Email",
		"condition": "doc.contact_email",
		"recipients": [{"receiver_by_document_field": "contact_email"}],
		"message": _CUSTOMER_ORDER_CANCELLED_MESSAGE,
	},
]


def _ensure_ecommerce_notifications():
	"""Install (or refresh) the five e-commerce Notifications above."""
	if not frappe.db.exists("DocType", "Notification"):
		return
	for spec in _ECOMMERCE_NOTIFICATIONS:
		_upsert_notification(spec)


def _upsert_notification(spec):
	base = {
		"enabled": 1,
		"is_standard": 0,
	}
	try:
		if frappe.db.exists("Notification", spec["name"]):
			doc = frappe.get_doc("Notification", spec["name"])
			doc.subject = spec["subject"]
			doc.document_type = spec["document_type"]
			doc.event = spec["event"]
			doc.channel = spec["channel"]
			doc.message = spec["message"]
			doc.condition = spec.get("condition") or ""
			doc.send_system_notification = spec.get("send_system_notification") or 0
			doc.enabled = 1
			# Replace recipients wholesale so the source of truth is this file
			doc.set("recipients", [])
			for r in spec.get("recipients", []):
				doc.append("recipients", r)
			doc.flags.ignore_permissions = True
			doc.save(ignore_permissions=True)
			return
		payload = {
			"doctype": "Notification",
			"name": spec["name"],
			"subject": spec["subject"],
			"document_type": spec["document_type"],
			"event": spec["event"],
			"channel": spec["channel"],
			"message": spec["message"],
			"recipients": spec.get("recipients", []),
		}
		if spec.get("condition"):
			payload["condition"] = spec["condition"]
		if spec.get("send_system_notification"):
			payload["send_system_notification"] = 1
		payload.update(base)
		frappe.get_doc(payload).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			f"Akwaaba: notification '{spec['name']}' install failed",
		)


# ---------------------------------------------------------------------------
# Client Script — Hover Video preview on Website Item form
# ---------------------------------------------------------------------------

_HOVER_VIDEO_SCRIPT_NAME = "Akwaaba Hover Video Preview"

_HOVER_VIDEO_SCRIPT = """
// Small quality-of-life polish on Website Item's Hover Video field:
//   - Renders a live preview player under the field so the admin can
//     confirm the clip looks right before saving.
//   - Warns if the file is > 8 MB or the aspect ratio isn't 3:4-ish.
frappe.ui.form.on("Website Item", {
    refresh(frm) {
        _renderHoverVideoPreview(frm);
    },
    hover_video_file(frm) {
        _renderHoverVideoPreview(frm);
    },
    hover_video_url(frm) {
        _renderHoverVideoPreview(frm);
    },
});

function _renderHoverVideoPreview(frm) {
    const container_id = "akw-hover-video-preview";
    $("#" + container_id).remove();

    const src = frm.doc.hover_video_url || frm.doc.hover_video_file;
    if (!src) return;

    const wrapper = frm.fields_dict.hover_video_url && frm.fields_dict.hover_video_url.$wrapper;
    if (!wrapper) return;

    const $preview = $(`
        <div id="${container_id}" class="mt-2 mb-3" style="max-width: 260px;">
            <div class="text-muted small mb-1">Preview:</div>
            <video src="${frappe.utils.escape_html(src)}"
                muted loop playsinline autoplay
                style="width: 100%; aspect-ratio: 3/4; object-fit: cover; background: #F7F1E7;">
            </video>
            <div class="akw-video-meta text-muted small mt-1"></div>
        </div>
    `);
    wrapper.append($preview);

    const video = $preview.find("video")[0];
    video.addEventListener("loadedmetadata", () => {
        const w = video.videoWidth, h = video.videoHeight;
        const ratio = w && h ? (w / h) : 0;
        const target = 3 / 4;
        const off = Math.abs(ratio - target);
        let notes = [`${w}×${h}`, `${video.duration.toFixed(1)}s`];
        if (off > 0.15) notes.push(`⚠ aspect ${ratio.toFixed(2)} (want ~${target.toFixed(2)}, i.e. 3:4)`);
        $preview.find(".akw-video-meta").html(notes.join(" · "));
    });
    video.addEventListener("error", () => {
        $preview.find(".akw-video-meta").html("⚠ Couldn't load — check the URL or upload.");
    });

    if (src.startsWith("/files/") || src.startsWith("/private/files/")) {
        frappe.db.get_list("File", {
            filters: { file_url: src },
            fields: ["file_size"],
            limit: 1,
        }).then(rows => {
            if (rows && rows.length && rows[0].file_size) {
                const mb = rows[0].file_size / (1024 * 1024);
                let note = `${mb.toFixed(1)} MB`;
                if (mb > 8) note += " ⚠ heavy — consider compressing";
                $preview.find(".akw-video-meta").append(" · " + note);
            }
        });
    }
}
""".strip()


def _ensure_hover_video_client_script():
	if not frappe.db.exists("DocType", "Client Script"):
		return
	_upsert_client_script(_HOVER_VIDEO_SCRIPT_NAME, "Website Item", _HOVER_VIDEO_SCRIPT)


# ---------------------------------------------------------------------------
# Server Script — reverse order (SO → SI → PE cascade cancel)
# ---------------------------------------------------------------------------
#
# Runs in Frappe's Server Script sandbox (safe_exec). Requires the site to
# have Server Scripts enabled — set `server_script_enabled: 1` in
# common_site_config.json or site_config.json, or the `bench` toggle:
#     bench --site <site> set-config server_script_enabled 1
#
# Called from the SO form Client Script via
#     frappe.call({ method: "akwaaba_reverse_order", args: {...} })
# ---------------------------------------------------------------------------

_REVERSE_SERVER_SCRIPT_NAME = "akwaaba_reverse_order"

_REVERSE_SERVER_SCRIPT = """
order_name = (frappe.form_dict.get("order_name") or "").strip()
reason = (frappe.form_dict.get("reason") or "").strip()

if not order_name:
    frappe.throw("Missing order name.")

roles = frappe.get_roles(frappe.session.user)
if not any(r in roles for r in ("System Manager", "Sales Manager", "Accounts Manager")):
    frappe.throw("You need a Sales/Accounts/System Manager role to reverse an order.")

if not frappe.db.exists("Sales Order", order_name):
    frappe.throw("Order not found: " + order_name)

result = {"cancelled": [], "notes": []}

# Find the Sales Invoice for this order (if any)
si_rows = frappe.get_all(
    "Sales Invoice Item",
    filters={"sales_order": order_name, "docstatus": ["!=", 2]},
    fields=["parent"],
    limit=1,
)
si_name = si_rows[0]["parent"] if si_rows else None

# Find the Payment Entry for that SI (if any)
pe_name = None
if si_name:
    pe_rows = frappe.get_all(
        "Payment Entry Reference",
        filters={
            "reference_name": si_name,
            "reference_doctype": "Sales Invoice",
            "docstatus": ["!=", 2],
        },
        fields=["parent"],
        limit=1,
    )
    pe_name = pe_rows[0]["parent"] if pe_rows else None

# Cancel in dependency order: PE → SI → SO
if pe_name:
    pe = frappe.get_doc("Payment Entry", pe_name)
    if pe.docstatus == 1:
        try:
            pe.cancel()
            result["cancelled"].append("Payment Entry " + pe_name)
        except Exception as e:
            result["notes"].append("PE " + pe_name + " cancel failed: " + str(e)[:120])

if si_name:
    si = frappe.get_doc("Sales Invoice", si_name)
    if si.docstatus == 1:
        try:
            si.cancel()
            result["cancelled"].append("Sales Invoice " + si_name)
        except Exception as e:
            result["notes"].append("SI " + si_name + " cancel failed: " + str(e)[:120])

so = frappe.get_doc("Sales Order", order_name)
if so.docstatus == 1:
    try:
        so.cancel()
        result["cancelled"].append("Sales Order " + order_name)
    except Exception as e:
        result["notes"].append("SO " + order_name + " cancel failed: " + str(e)[:120])

if reason:
    # Append the reversal note as a Comment (internal-only), NOT to terms
    # which appears on customer-facing print templates.
    try:
        frappe.get_doc({
            "doctype": "Comment",
            "comment_type": "Comment",
            "reference_doctype": "Sales Order",
            "reference_name": order_name,
            "content": "Reversed by " + frappe.session.user + ": " + reason,
        }).insert(ignore_permissions=True)
    except Exception:
        pass

if not result["cancelled"]:
    result["notes"].insert(
        0,
        "Nothing to cancel — order and its accounting docs were already in Draft or Cancelled.",
    )

frappe.response["message"] = result
""".strip()


def _ensure_reverse_order_server_script():
	if not frappe.db.exists("DocType", "Server Script"):
		return
	try:
		if frappe.db.exists("Server Script", _REVERSE_SERVER_SCRIPT_NAME):
			doc = frappe.get_doc("Server Script", _REVERSE_SERVER_SCRIPT_NAME)
			doc.script = _REVERSE_SERVER_SCRIPT
			doc.script_type = "API"
			doc.api_method = _REVERSE_SERVER_SCRIPT_NAME
			doc.allow_guest = 0
			doc.disabled = 0
			doc.flags.ignore_permissions = True
			doc.save(ignore_permissions=True)
			return
		frappe.get_doc({
			"doctype": "Server Script",
			"name": _REVERSE_SERVER_SCRIPT_NAME,
			"script_type": "API",
			"api_method": _REVERSE_SERVER_SCRIPT_NAME,
			"allow_guest": 0,
			"disabled": 0,
			"script": _REVERSE_SERVER_SCRIPT,
		}).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			"Akwaaba: reverse-order Server Script install failed",
		)


# ---------------------------------------------------------------------------
# Client Script — Reverse Full Order button on Sales Order
# ---------------------------------------------------------------------------

_REVERSAL_CLIENT_SCRIPT_NAME = "Akwaaba Reverse Order Button"

_REVERSAL_CLIENT_SCRIPT = """
// Adds a 'Reverse Full Order' action to Sales Orders that came in from
// the Akwaaba storefront (identified by 'Paystack reference' in Terms).
frappe.ui.form.on("Sales Order", {
    refresh(frm) {
        if (frm.doc.docstatus !== 1) return;
        if (!/Paystack reference/i.test(frm.doc.terms || "")) return;

        frm.add_custom_button(__("Reverse Full Order (Paystack)"), () => {
            frappe.prompt(
                [{
                    fieldtype: "Small Text",
                    label: __("Reason (required)"),
                    fieldname: "reason",
                    reqd: 1,
                }],
                (values) => {
                    frappe.call({
                        method: "akwaaba_reverse_order",
                        args: {
                            order_name: frm.doc.name,
                            reason: values.reason,
                        },
                        freeze: true,
                        freeze_message: __("Reversing…"),
                        callback: (r) => {
                            const m = r.message || {};
                            let html = "";
                            if (m.cancelled && m.cancelled.length) {
                                html += "<p><b>Cancelled:</b></p><ul>" +
                                    m.cancelled.map(x => `<li>${frappe.utils.escape_html(x)}</li>`).join("") +
                                    "</ul>";
                            }
                            if (m.notes && m.notes.length) {
                                html += "<p><b>Notes:</b></p><ul>" +
                                    m.notes.map(x => `<li>${frappe.utils.escape_html(x)}</li>`).join("") +
                                    "</ul>";
                            }
                            frappe.msgprint({
                                title: __("Reversal Result"),
                                message: html || __("No changes made."),
                                indicator: (m.cancelled && m.cancelled.length) ? "orange" : "blue",
                            });
                            frm.reload_doc();
                        },
                    });
                },
                __("Reverse this order?"),
                __("Reverse"),
            );
        }, __("Actions"));
    },
});
""".strip()


def _ensure_reverse_order_client_script():
	if not frappe.db.exists("DocType", "Client Script"):
		return
	_upsert_client_script(_REVERSAL_CLIENT_SCRIPT_NAME, "Sales Order", _REVERSAL_CLIENT_SCRIPT)


# ---------------------------------------------------------------------------
# Client Script — Confirm / Reject MoMo Payment buttons on Sales Order
# ---------------------------------------------------------------------------

_MOMO_CONFIRM_CLIENT_SCRIPT_NAME = "Akwaaba Confirm MoMo Payment"

_MOMO_CONFIRM_CLIENT_SCRIPT = """
// Direct-MoMo confirmation controls. Shown while an order is unpaid and a
// customer has claimed (or is about to claim) a MoMo payment. See momo.py.
frappe.ui.form.on("Sales Order", {
    refresh(frm) {
        if (frm.doc.docstatus === 1) return;           // already submitted = paid
        const ps = frm.doc.payment_status || "";

        // Highlight orders waiting on us.
        if (ps === "Awaiting Confirmation") {
            frm.dashboard.set_headline(
                __("⏳ Customer says they've paid by MoMo. Check your MoMo, then Confirm."));
        }

        frm.add_custom_button(__("Confirm MoMo Payment"), () => {
            frappe.prompt(
                [
                    {
                        fieldtype: "Currency",
                        label: __("Amount received (leave as-is for full total)"),
                        fieldname: "amount",
                        default: frm.doc.grand_total,
                    },
                    {
                        fieldtype: "Data",
                        label: __("MoMo transaction id (optional)"),
                        fieldname: "txn_id",
                        default: frm.doc.momo_txn_id,
                    },
                ],
                (values) => {
                    frappe.call({
                        method: "akwaaba_theme.momo.confirm_momo_payment",
                        args: { order_name: frm.doc.name, amount: values.amount, txn_id: values.txn_id },
                        freeze: true,
                        freeze_message: __("Confirming payment…"),
                        callback: (r) => {
                            const m = r.message || {};
                            frappe.msgprint({
                                title: __("Payment Confirmed"),
                                message: (m.notes || []).map(frappe.utils.escape_html).join("<br>") ||
                                    __("Order marked paid."),
                                indicator: "green",
                            });
                            frm.reload_doc();
                        },
                    });
                },
                __("Confirm you've received this MoMo payment"),
                __("Confirm"),
            );
        }, __("MoMo")).addClass("btn-primary");

        frm.add_custom_button(__("Reject MoMo Claim"), () => {
            frappe.prompt(
                [{ fieldtype: "Small Text", label: __("Reason"), fieldname: "reason", reqd: 1 }],
                (values) => {
                    frappe.call({
                        method: "akwaaba_theme.momo.reject_momo_payment",
                        args: { order_name: frm.doc.name, reason: values.reason },
                        freeze: true,
                        callback: () => frm.reload_doc(),
                    });
                },
                __("Reject this payment claim?"),
                __("Reject"),
            );
        }, __("MoMo"));
    },
});
""".strip()


def _ensure_momo_confirm_client_script():
	if not frappe.db.exists("DocType", "Client Script"):
		return
	_upsert_client_script(_MOMO_CONFIRM_CLIENT_SCRIPT_NAME, "Sales Order",
	                      _MOMO_CONFIRM_CLIENT_SCRIPT)


def _upsert_client_script(name, dt, script):
	try:
		if frappe.db.exists("Client Script", name):
			doc = frappe.get_doc("Client Script", name)
			doc.script = script
			doc.dt = dt
			doc.view = "Form"
			doc.enabled = 1
			doc.flags.ignore_permissions = True
			doc.save(ignore_permissions=True)
			return
		frappe.get_doc({
			"doctype": "Client Script",
			"name": name,
			"dt": dt,
			"view": "Form",
			"enabled": 1,
			"script": script,
		}).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			f"Akwaaba: client script '{name}' install failed",
		)


# ---------------------------------------------------------------------------
# Category route redirects
# ---------------------------------------------------------------------------

def seed_category_redirects():
	"""Seed Website Settings → Route Redirects so each published Item Group
	route (e.g. /accessories) 301s to the themed /shop filtered by that
	category. This retires webshop's stock Item Group generator pages, whose
	Frappe-default styling clashes with the theme.

	Idempotent — existing sources are left untouched. Re-run after adding a
	new collection:
	    bench --site <site> execute akwaaba_theme.install.seed_category_redirects
	"""
	groups = frappe.get_all(
		"Item Group",
		filters={"show_in_website": 1, "is_group": 0},
		fields=["name", "route"],
	)
	ws = frappe.get_single("Website Settings")
	existing = {r.source for r in (ws.route_redirects or [])}
	added = 0
	for g in groups:
		route = (g.route or "").strip("/")
		if not route:
			continue
		# Anchor with $ so "/accessories" doesn't also swallow
		# "/accessories-hats" or the "/all-products/index.js" asset path.
		source = "/" + route + "$"
		if source in existing:
			continue
		ws.append("route_redirects", {
			"source": source,
			"target": "/shop?category=" + route,
			"redirect_http_status": 301,
		})
		existing.add(source)
		added += 1
	if added:
		ws.save(ignore_permissions=True)
		frappe.db.commit()
	print(f"Akwaaba: category redirects seeded (+{added}).")
