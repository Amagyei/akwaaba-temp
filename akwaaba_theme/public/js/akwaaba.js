// AKWAABA — storefront interactions
// Cart is guest-friendly: state lives in `akw_cart` cookie as JSON
// { item_code: qty }. Server reads it on /cart and place_order.
(function () {
	"use strict";

	// =======================================================================
	// COOKIE CART
	// =======================================================================
	const CART_COOKIE = "akw_cart";
	const CART_EXPIRY_DAYS = 30;
	const PREF_SIZE_COOKIE = "akw_pref_size";
	const PREF_SIZE_EXPIRY_DAYS = 90;

	function readPrefSize() {
		const m = document.cookie.match(new RegExp("(?:^|;\\s*)" + PREF_SIZE_COOKIE + "=([^;]+)"));
		return m ? decodeURIComponent(m[1]) : "";
	}
	function writePrefSize(size) {
		if (!size) return;
		const d = new Date();
		d.setTime(d.getTime() + PREF_SIZE_EXPIRY_DAYS * 24 * 60 * 60 * 1000);
		document.cookie = `${PREF_SIZE_COOKIE}=${encodeURIComponent(size)}; expires=${d.toUTCString()}; path=/; SameSite=Lax`;
	}

	function readCart() {
		const m = document.cookie.match(new RegExp("(?:^|;\\s*)" + CART_COOKIE + "=([^;]+)"));
		if (!m) return {};
		try {
			return JSON.parse(decodeURIComponent(m[1])) || {};
		} catch (_) {
			return {};
		}
	}

	function writeCart(cart) {
		const value = encodeURIComponent(JSON.stringify(cart));
		const d = new Date();
		d.setTime(d.getTime() + CART_EXPIRY_DAYS * 24 * 60 * 60 * 1000);
		document.cookie = `${CART_COOKIE}=${value}; expires=${d.toUTCString()}; path=/; SameSite=Lax`;
	}

	function cartTotalItems(cart) {
		return Object.values(cart).reduce(function (a, q) { return a + (parseInt(q, 10) || 0); }, 0);
	}

	function setQty(itemCode, qty) {
		const cart = readCart();
		if (qty <= 0) delete cart[itemCode];
		else cart[itemCode] = qty;
		writeCart(cart);
		return cart;
	}

	// =======================================================================
	// NAV CART ICON
	// =======================================================================
	function injectNavCartIcon() {
		const nav = document.querySelector(".navbar .container, .navbar > .container-fluid, .navbar");
		if (!nav) return;
		if (nav.querySelector(".akw-nav-cart")) return;

		const a = document.createElement("a");
		a.className = "akw-nav-cart";
		a.href = "/cart";
		a.setAttribute("aria-label", "View cart");
		a.innerHTML = `
			<svg class="akw-nav-cart__icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true">
				<path d="M5 6 L19 6 L17.5 17 L6.5 17 Z"/>
				<circle cx="9" cy="20.5" r="1.2" fill="currentColor" stroke="none"/>
				<circle cx="15" cy="20.5" r="1.2" fill="currentColor" stroke="none"/>
				<path d="M9 6 V4 A3 3 0 0 1 15 4 V6"/>
			</svg>
			<span class="akw-nav-cart__badge" hidden>0</span>
		`;
		nav.appendChild(a);
		updateNavCartBadge();
	}

	function updateNavCartBadge() {
		const badge = document.querySelector(".akw-nav-cart__badge");
		if (!badge) return;
		const n = cartTotalItems(readCart());
		if (n > 0) {
			badge.textContent = n > 99 ? "99+" : String(n);
			badge.hidden = false;
		} else {
			badge.hidden = true;
		}
	}

	// =======================================================================
	// CARD ADD BUTTON + STEPPER + SIZE PICKER
	// =======================================================================
	//
	// Pattern A (reveal-in-place):
	//   • single-SKU items: tap [Add] → morph to stepper (existing behavior)
	//   • variant items: tap [Select size] → chip strip appears in the footer
	//     row, replacing price + button → tap chip → resolves to variant
	//     item_code → morph to stepper with a "Size M" tag on the left.
	//
	// The card's footer height is fixed in CSS so the grid never reflows on
	// state change. All variant data is embedded on the card via
	// data-variants (JSON) at server-render time — no network on tap.
	// =======================================================================

	function getVariants(card) {
		const raw = card.getAttribute("data-variants");
		if (!raw) return [];
		try { return JSON.parse(raw) || []; } catch (_) { return []; }
	}

	function hasVariants(card) {
		return card.hasAttribute("data-template-code");
	}

	// State setter — one function, three states.
	// state: "rest" | "sizes" | "added"
	function setCardState(card, state, opts) {
		opts = opts || {};
		const addBtn = card.querySelector(".akw-card__cta.-add");
		const stepper = card.querySelector(".akw-card__stepper");
		const sizes = card.querySelector(".akw-card__sizes");
		const priceEl = card.querySelector('[data-role="price"]');
		const sizeTag = card.querySelector('[data-role="size-tag"]');

		// Reset all
		if (addBtn) addBtn.hidden = true;
		if (stepper) stepper.hidden = true;
		if (sizes) sizes.hidden = true;
		if (priceEl) priceEl.hidden = false;
		if (sizeTag) { sizeTag.hidden = true; sizeTag.textContent = ""; }
		card.classList.remove("is-picking-size", "is-in-cart");

		if (state === "rest") {
			if (addBtn) addBtn.hidden = false;
		} else if (state === "sizes") {
			// Sizes strip spans the whole footer — hide price + button
			if (sizes) {
				sizes.hidden = false;
				// Pre-highlight remembered size if available and in stock
				const pref = readPrefSize();
				sizes.querySelectorAll(".akw-card__size").forEach(function (chip) {
					chip.classList.toggle(
						"-preferred",
						!!pref && chip.getAttribute("data-size") === pref && !chip.classList.contains("-oos"),
					);
				});
			}
			if (priceEl) priceEl.hidden = true;
			card.classList.add("is-picking-size");
		} else if (state === "added") {
			// Show stepper on the right; if variant, replace price with size tag
			if (stepper) {
				stepper.hidden = false;
				const qtyEl = stepper.querySelector(".akw-card__qty");
				if (qtyEl) qtyEl.textContent = String(opts.qty || 1);
				if (opts.variantCode) stepper.setAttribute("data-item-code", opts.variantCode);
			}
			if (opts.size && sizeTag) {
				sizeTag.textContent = opts.size;
				sizeTag.hidden = false;
				if (priceEl) priceEl.hidden = true;
			}
			card.classList.add("is-in-cart");
		}
	}

	function hydrateCards() {
		const cart = readCart();

		document.querySelectorAll(".akw-card[data-item-code]").forEach(function (card) {
			// For variant templates, find any variant in cart
			if (hasVariants(card)) {
				const variants = getVariants(card);
				for (let i = 0; i < variants.length; i++) {
					const v = variants[i];
					const q = parseInt(cart[v.code], 10) || 0;
					if (q > 0) {
						setCardState(card, "added", { qty: q, variantCode: v.code, size: v.size });
						return;
					}
				}
				setCardState(card, "rest");
				return;
			}

			// Single-SKU
			const code = card.getAttribute("data-item-code");
			const qty = parseInt(cart[code], 10) || 0;
			if (qty > 0) setCardState(card, "added", { qty: qty });
			else setCardState(card, "rest");
		});
	}

	function bindAddClick() {
		document.addEventListener("click", function (e) {
			const btn = e.target.closest(".akw-card__cta.-add");
			if (!btn || btn.disabled) return;
			const card = btn.closest(".akw-card");
			if (!card) return;

			e.preventDefault();
			e.stopPropagation();
			e.stopImmediatePropagation();

			if (hasVariants(card)) {
				// Close any other open size pickers before opening this one
				document.querySelectorAll(".akw-card.is-picking-size").forEach(function (c) {
					if (c !== card) setCardState(c, "rest");
				});
				setCardState(card, "sizes");
				return;
			}

			const itemCode = btn.getAttribute("data-item-code");
			if (!itemCode) return;
			setQty(itemCode, 1);
			setCardState(card, "added", { qty: 1 });
			updateNavCartBadge();
		}, true);
	}

	function bindSizePicker() {
		document.addEventListener("click", function (e) {
			// Cancel (× on the strip)
			const closeBtn = e.target.closest(".akw-card__size-close");
			if (closeBtn) {
				e.preventDefault();
				const card = closeBtn.closest(".akw-card");
				if (card) setCardState(card, "rest");
				return;
			}

			// Chip tap
			const chip = e.target.closest(".akw-card__size");
			if (!chip) return;
			const card = chip.closest(".akw-card");
			if (!card) return;
			e.preventDefault();
			e.stopPropagation();

			if (chip.classList.contains("-oos")) {
				// TODO: waitlist capture — for now, gently shake and stay open
				chip.animate(
					[
						{ transform: "translateX(0)" },
						{ transform: "translateX(-3px)" },
						{ transform: "translateX(3px)" },
						{ transform: "translateX(0)" },
					],
					{ duration: 220 },
				);
				return;
			}

			const variantCode = chip.getAttribute("data-variant-code");
			const size = chip.getAttribute("data-size");
			if (!variantCode) return;

			writePrefSize(size);
			setQty(variantCode, 1);
			setCardState(card, "added", { qty: 1, variantCode: variantCode, size: size });
			updateNavCartBadge();
		}, true);

		// Dismiss open picker when tapping outside any card
		document.addEventListener("click", function (e) {
			if (e.target.closest(".akw-card")) return;
			document.querySelectorAll(".akw-card.is-picking-size").forEach(function (c) {
				setCardState(c, "rest");
			});
		});
	}

	function bindStepperEvents() {
		document.addEventListener("click", function (e) {
			const step = e.target.closest(".akw-card__step");
			if (!step) return;
			e.preventDefault();
			e.stopPropagation();

			const stepper = step.closest(".akw-card__stepper");
			const card = step.closest(".akw-card");
			if (!stepper || !card) return;

			const itemCode = stepper.getAttribute("data-item-code");
			const qtyEl = stepper.querySelector(".akw-card__qty");
			const currentQty = parseInt(qtyEl.textContent, 10) || 0;
			const direction = step.getAttribute("data-step");
			const newQty = direction === "up" ? currentQty + 1 : Math.max(currentQty - 1, 0);
			if (newQty === currentQty) return;

			setQty(itemCode, newQty);
			if (newQty === 0) {
				// If this was a variant, drop back to the template's rest state
				setCardState(card, "rest");
			} else {
				qtyEl.textContent = String(newQty);
			}
			updateNavCartBadge();
		});
	}

	// =======================================================================
	// /CART PAGE — line-item quantity controls
	// =======================================================================
	function bindCartLineControls() {
		document.querySelectorAll(".akw-cart-line").forEach(function (line) {
			const itemCode = line.getAttribute("data-item-code");
			const qtyEl = line.querySelector(".akw-cart-line__qty");
			line.querySelectorAll(".akw-cart-line__step").forEach(function (btn) {
				btn.addEventListener("click", function (e) {
					e.preventDefault();
					const direction = btn.getAttribute("data-step");
					const current = parseInt(qtyEl.textContent, 10) || 0;
					const next = direction === "up" ? current + 1 : Math.max(current - 1, 0);
					if (next === current) return;
					setQty(itemCode, next);
					if (next === 0) {
						line.remove();
					} else {
						qtyEl.textContent = String(next);
						const sub = line.querySelector(".akw-cart-line__subtotal-amount");
						if (sub) {
							const unit = parseFloat(line.getAttribute("data-unit-price")) || 0;
							sub.textContent = (unit * next).toLocaleString("en-US", { maximumFractionDigits: 0 });
						}
					}
					updateNavCartBadge();
					recomputeCartTotals();
				});
			});
			const removeBtn = line.querySelector(".akw-cart-line__remove");
			if (removeBtn) {
				removeBtn.addEventListener("click", function (e) {
					e.preventDefault();
					setQty(itemCode, 0);
					line.remove();
					updateNavCartBadge();
					recomputeCartTotals();
				});
			}
		});
	}

	function recomputeCartTotals() {
		const rows = document.querySelectorAll(".akw-cart-line");
		let total = 0;
		let count = 0;
		rows.forEach(function (line) {
			const unit = parseFloat(line.getAttribute("data-unit-price")) || 0;
			const qty = parseInt(line.querySelector(".akw-cart-line__qty").textContent, 10) || 0;
			total += unit * qty;
			count += qty;
		});
		const totalEl = document.querySelector(".akw-cart__grand-total .akw-cart__grand-amount");
		const countEl = document.querySelector(".akw-cart__item-count");
		if (totalEl) totalEl.textContent = total.toLocaleString("en-US", { maximumFractionDigits: 0 });
		if (countEl) countEl.textContent = String(count);
		if (rows.length === 0) {
			const empty = document.querySelector(".akw-cart__empty");
			const layout = document.querySelector(".akw-cart__layout");
			if (empty) empty.hidden = false;
			if (layout) layout.hidden = true;
		}
	}

	// =======================================================================
	// CHECKOUT FORM SUBMIT — POST cart + form to place_order
	// =======================================================================
	function bindCheckoutSubmit() {
		const form = document.querySelector(".akw-cart__form");
		if (!form) return;
		form.addEventListener("submit", function (e) {
			e.preventDefault();
			const submitBtn = form.querySelector('button[type="submit"]');
			if (submitBtn) {
				submitBtn.disabled = true;
				submitBtn.classList.add("is-loading");
			}

			const cart = readCart();
			if (Object.keys(cart).length === 0) {
				alert("Your cart is empty.");
				if (submitBtn) { submitBtn.disabled = false; submitBtn.classList.remove("is-loading"); }
				return;
			}

			const formData = new FormData(form);
			const payload = {
				items: JSON.stringify(cart),
				full_name: formData.get("full_name") || "",
				phone: formData.get("phone") || "",
				email: formData.get("email") || "",
				address_line: formData.get("address_line") || "",
				city: formData.get("city") || "",
				region: formData.get("region") || "",
				notes: formData.get("notes") || "",
			};

			fetch("/api/method/akwaaba_theme.api.place_order", {
				method: "POST",
				headers: {
					"Content-Type": "application/x-www-form-urlencoded",
					"X-Requested-With": "XMLHttpRequest",
					"X-Frappe-CSRF-Token": (window.frappe && frappe.csrf_token) || "",
				},
				credentials: "same-origin",
				body: new URLSearchParams(payload).toString(),
			})
				.then(function (r) { return r.json().then(function (j) { return { ok: r.ok, body: j }; }); })
				.then(function (res) {
					if (!res.ok || !res.body || !res.body.message) {
						throw new Error((res.body && res.body.exception) || "Order failed");
					}
					const order = res.body.message;
					// Clear cart cookie
					writeCart({});
					window.location.href = "/order/" + encodeURIComponent(order.name);
				})
				.catch(function (err) {
					if (submitBtn) { submitBtn.disabled = false; submitBtn.classList.remove("is-loading"); }
					const msg = form.querySelector(".akw-cart__error");
					if (msg) {
						msg.textContent = err.message || "Something went wrong.";
						msg.hidden = false;
					} else {
						alert(err.message || "Something went wrong.");
					}
				});
		});
	}

	// =======================================================================
	// NEWSLETTER (unchanged)
	// =======================================================================
	document.addEventListener("submit", function (e) {
		const form = e.target;
		if (!form.matches(".akwaaba-newsletter__form")) return;
		e.preventDefault();
		const input = form.querySelector("input");
		if (window.frappe && frappe.show_alert) {
			frappe.show_alert({ message: "Medaase! We'll be in touch soon.", indicator: "green" });
		} else {
			alert("Medaase! We'll be in touch soon.");
		}
		if (input) input.value = "";
	});

	// =======================================================================
	// CARD HOVER VIDEOS (unchanged)
	// =======================================================================
	function initAkwCardVideos() {
		const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
		const reducedData = window.matchMedia("(prefers-reduced-data: reduce)").matches;
		if (reducedMotion || reducedData) return;

		const cards = document.querySelectorAll(".akw-card");
		if (!cards.length) return;

		const hoverCapable = window.matchMedia("(hover: hover) and (pointer: fine)").matches;
		const io = !hoverCapable && "IntersectionObserver" in window
			? new IntersectionObserver(onIntersect, { threshold: 0.6, rootMargin: "0px 0px -8% 0px" })
			: null;

		cards.forEach(function (card) {
			const video = card.querySelector(".akw-card__video");
			if (!video) return;
			const src = video.getAttribute("data-src");
			if (!src) return;

			let sourceAdded = false;
			const ensureSource = function () {
				if (sourceAdded) return;
				const source = document.createElement("source");
				source.src = src;
				source.type = src.endsWith(".webm") ? "video/webm" : "video/mp4";
				video.appendChild(source);
				video.load();
				sourceAdded = true;
			};

			const play = function () {
				ensureSource();
				const p = video.play();
				if (p && p.then) p.then(function () { card.classList.add("is-playing"); }).catch(function () {});
			};
			const pause = function () {
				video.pause();
				if (video.currentTime > 0) video.currentTime = 0;
				card.classList.remove("is-playing");
			};

			video.addEventListener("error", function () {
				card.classList.add("is-video-broken");
				video.remove();
				const badge = card.querySelector(".akw-card__media-badge");
				if (badge) badge.remove();
			}, { once: true });

			if (hoverCapable) {
				card.addEventListener("mouseenter", play);
				card.addEventListener("mouseleave", pause);
				card.addEventListener("focusin", play);
				card.addEventListener("focusout", pause);
			} else if (io) {
				card._akwPlay = play;
				card._akwPause = pause;
				io.observe(card);
			}
		});

		function onIntersect(entries) {
			entries.forEach(function (entry) {
				const card = entry.target;
				if (entry.isIntersecting) card._akwPlay && card._akwPlay();
				else card._akwPause && card._akwPause();
			});
		}
	}

	// =======================================================================
	// HERO MEDIA CAROUSEL (items flagged "Feature on Hero")
	// =======================================================================
	function initAkwHeroCarousel() {
		const hero = document.querySelector(".akwaaba-hero.-has-media");
		if (!hero) return;
		const slides = Array.prototype.slice.call(
			hero.querySelectorAll(".akwaaba-hero__slide")
		);
		if (!slides.length) return;

		const dots = Array.prototype.slice.call(hero.querySelectorAll(".akwaaba-hero__dot"));
		const prev = hero.querySelector(".akwaaba-hero__arrow.-prev");
		const next = hero.querySelector(".akwaaba-hero__arrow.-next");

		const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
		const reducedData = window.matchMedia("(prefers-reduced-data: reduce)").matches;
		const INTERVAL = 6000;

		let index = 0;
		let timer = null;

		function ensureVideoSource(video) {
			if (!video || video._akwLoaded) return;
			const src = video.getAttribute("data-src");
			if (!src) return;
			const source = document.createElement("source");
			source.src = src;
			source.type = src.endsWith(".webm") ? "video/webm" : "video/mp4";
			video.appendChild(source);
			video.load();
			video._akwLoaded = true;
			video.addEventListener("error", function () {
				const slide = video.closest(".akwaaba-hero__slide");
				video.remove();
				if (slide) slide.classList.remove("is-playing");
			}, { once: true });
		}

		function syncVideos() {
			// Reduced motion/data → leave the poster/image still, no playback.
			if (reducedMotion || reducedData) return;
			slides.forEach(function (slide, n) {
				const video = slide.querySelector(".akwaaba-hero__video");
				if (!video) return;
				if (n === index) {
					ensureVideoSource(video);
					const p = video.play();
					if (p && p.then) {
						p.then(function () { slide.classList.add("is-playing"); }).catch(function () {});
					}
				} else {
					video.pause();
					if (video.currentTime > 0) video.currentTime = 0;
					slide.classList.remove("is-playing");
				}
			});
		}

		function show(i) {
			index = (i + slides.length) % slides.length;
			slides.forEach(function (slide, n) {
				const on = n === index;
				slide.classList.toggle("is-active", on);
				if (on) slide.setAttribute("aria-current", "true");
				else slide.removeAttribute("aria-current");
			});
			dots.forEach(function (dot, n) {
				dot.classList.toggle("is-active", n === index);
			});
			syncVideos();
		}

		function go(delta) { show(index + delta); }

		function stop() {
			if (timer) { window.clearInterval(timer); timer = null; }
		}
		function start() {
			stop();
			if (reducedMotion || slides.length < 2) return;
			timer = window.setInterval(function () { go(1); }, INTERVAL);
		}

		if (prev) prev.addEventListener("click", function (e) { e.preventDefault(); go(-1); start(); });
		if (next) next.addEventListener("click", function (e) { e.preventDefault(); go(1); start(); });
		dots.forEach(function (dot) {
			dot.addEventListener("click", function (e) {
				e.preventDefault();
				show(parseInt(dot.getAttribute("data-index"), 10) || 0);
				start();
			});
		});

		// Pause auto-advance while the visitor is engaging with the hero,
		// or when the tab is hidden.
		hero.addEventListener("mouseenter", stop);
		hero.addEventListener("mouseleave", start);
		document.addEventListener("visibilitychange", function () {
			if (document.hidden) stop(); else start();
		});

		show(0);
		start();
	}

	function initNavDrawer() {
		document.addEventListener("click", function (e) {
			const link = e.target.closest(".navbar-collapse .nav-link");
			if (!link) return;
			const collapse = link.closest(".navbar-collapse");
			if (collapse && collapse.classList.contains("show")) {
				collapse.classList.remove("show");
			}
		});
	}

	// =======================================================================
	// PAYSTACK — Pay Now button on /order/<name>
	// =======================================================================
	function bindPayNow() {
		document.addEventListener("click", function (e) {
			const btn = e.target.closest("[data-akw-pay-order]");
			if (!btn || btn.disabled) return;
			e.preventDefault();

			const orderName = btn.getAttribute("data-akw-pay-order");
			if (!orderName) return;

			const errEl = document.querySelector(".akw-order__pay-error");
			if (errEl) { errEl.hidden = true; errEl.textContent = ""; }
			btn.disabled = true;
			btn.classList.add("is-loading");

			fetch("/api/method/akwaaba_theme.api.init_paystack_payment", {
				method: "POST",
				headers: {
					"Content-Type": "application/x-www-form-urlencoded",
					"X-Requested-With": "XMLHttpRequest",
					"X-Frappe-CSRF-Token": (window.frappe && frappe.csrf_token) || "",
				},
				credentials: "same-origin",
				body: new URLSearchParams({ order_name: orderName }).toString(),
			})
				.then(function (r) { return r.json().then(function (j) { return { ok: r.ok, body: j }; }); })
				.then(function (res) {
					const msg = (res.body && res.body.message) || {};
					if (msg.already_paid) {
						window.location.reload();
						return;
					}
					if (!res.ok || !msg.authorization_url) {
						throw new Error(
							(res.body && res.body._server_messages) ||
							(res.body && res.body.exception) ||
							"Couldn't start payment."
						);
					}
					window.location.href = msg.authorization_url;
				})
				.catch(function (err) {
					btn.disabled = false;
					btn.classList.remove("is-loading");
					if (errEl) {
						errEl.textContent = _readableError(err.message);
						errEl.hidden = false;
					}
				});
		});
	}

	function _readableError(raw) {
		if (!raw) return "Something went wrong. Please try again.";
		try {
			const parsed = JSON.parse(raw);
			if (Array.isArray(parsed) && parsed.length) {
				const first = JSON.parse(parsed[0]);
				return first.message || raw;
			}
		} catch (_) {}
		return String(raw).slice(0, 200);
	}

	// =======================================================================
	// PRODUCT DETAIL PAGE (/product/<slug>)
	// Reuses the same cookie cart as the cards. Size chips pick a variant;
	// Add to cart writes to the cookie and updates the nav badge.
	// =======================================================================
	function bindProductPage() {
		const pdp = document.querySelector(".akw-pdp");
		if (!pdp) return;

		const hasVariants = pdp.getAttribute("data-has-variants") === "1";
		const addBtn = pdp.querySelector(".akw-pdp__add");
		const priceEl = pdp.querySelector(".akw-pdp__price-amount");
		const ccyEl = pdp.querySelector(".akw-pdp__ccy");
		const hintEl = pdp.querySelector(".akw-pdp__hint");
		let activeCode = hasVariants ? null : pdp.getAttribute("data-item-code");

		function refreshAddState() {
			if (!addBtn) return;
			const blocked = hasVariants && !activeCode;
			addBtn.disabled = blocked;
			addBtn.classList.toggle("-disabled", blocked);
		}

		// Gallery thumbnails
		const hero = pdp.querySelector("#akw-pdp-hero");
		pdp.querySelectorAll(".akw-pdp__thumb").forEach(function (thumb) {
			thumb.addEventListener("click", function () {
				const src = thumb.getAttribute("data-src");
				if (hero && src) hero.src = src;
				pdp.querySelectorAll(".akw-pdp__thumb").forEach(function (t) { t.classList.remove("-active"); });
				thumb.classList.add("-active");
			});
		});

		// Size selection
		pdp.querySelectorAll(".akw-pdp__size").forEach(function (chip) {
			chip.addEventListener("click", function () {
				if (chip.getAttribute("data-oos") === "1") return;
				pdp.querySelectorAll(".akw-pdp__size").forEach(function (c) { c.classList.remove("-active"); });
				chip.classList.add("-active");
				activeCode = chip.getAttribute("data-variant-code");
				const disp = chip.getAttribute("data-price-display");
				if (disp && priceEl) {
					// data-price-display is "GHS 1,234" — split ccy from amount
					const parts = disp.split(/\s+/);
					if (parts.length > 1 && ccyEl) { ccyEl.textContent = parts[0]; priceEl.textContent = parts.slice(1).join(" "); }
					else { priceEl.textContent = disp; }
				}
				if (hintEl) hintEl.hidden = true;
				refreshAddState();
			});
		});

		// Preselect (arrived via a variant URL)
		const pre = pdp.getAttribute("data-preselect");
		if (pre) {
			const chip = pdp.querySelector('.akw-pdp__size[data-size="' + pre + '"]');
			if (chip) chip.click();
		}

		refreshAddState();

		if (addBtn) {
			addBtn.addEventListener("click", function () {
				if (hasVariants && !activeCode) {
					if (hintEl) hintEl.hidden = false;
					return;
				}
				const cart = readCart();
				const next = (parseInt(cart[activeCode], 10) || 0) + 1;
				setQty(activeCode, next);
				updateNavCartBadge();
				addBtn.classList.add("-added");
				addBtn.innerHTML = 'Added ✓ — <a href="/cart">View cart</a>';
			});
		}
	}

	// =======================================================================
	// BOOT
	// =======================================================================
	function boot() {
		injectNavCartIcon();
		hydrateCards();
		bindAddClick();
		bindSizePicker();
		bindStepperEvents();
		bindCartLineControls();
		bindCheckoutSubmit();
		bindPayNow();
		initAkwCardVideos();
		initAkwHeroCarousel();
		initNavDrawer();
		bindProductPage();
	}

	if (document.readyState === "loading") {
		document.addEventListener("DOMContentLoaded", boot);
	} else {
		boot();
	}
})();
