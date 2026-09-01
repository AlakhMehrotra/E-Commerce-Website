// ═══════════════════════════════════════════════════════════════════════
// Shri Jeevani Sarees — Frontend logic
// CHANGE (Phase 1 backend integration): the hardcoded `products` array and
// localStorage-based admin storage have been replaced with calls to the
// Flask API (/api/products, /api/admin/...). All UI, element IDs, classes
// and behavior are unchanged — only the data source changed.
// CHANGE (Phase 2): real customer accounts (/api/auth/*), a server-
// persisted cart (/api/cart/*) for logged-in customers, and a new
// wishlist feature (/api/wishlist/*). Guests can still browse, add to
// cart, and checkout exactly as before — nothing existing was removed.
// ═══════════════════════════════════════════════════════════════════════

const API_BASE = '';
// CHANGE (Phase 4): holds the reset token from a password-reset email link
// while the Reset Password form is showing.
let pendingResetToken = null;
// CHANGE: holds the identifier (email/phone) and signed OTP token between the password step and
// the OTP step of login, since verify-otp needs it to look the account up
// again statelessly on serverless (Vercel) hosting.
let pendingLoginIdentifier = null;
let pendingLoginOtpToken = null;
// CHANGE: holds the email and signed OTP token during signup OTP verification (account is created
// but locked until OTP is confirmed).
let pendingSignupEmail = null;
let pendingSignupOtpToken = null;
let products = [];
let cart = [];
let wishlist = [];          // Phase 2: array of full product objects
let wishlistIds = new Set(); // Phase 2: quick lookup for heart icon state
let currentPage = 'home';
let isAdminLoggedIn = false;

// Phase 2/3 CHANGE: checkout coupon + order state
let appliedCoupon = null;   // { code, discountType, discountValue, discount }
let currentReviewProductId = null;
let selectedReviewRating = 0;
let myOrdersCache = [];

// ─── XSS-safety helper ──────────────────────────────────────────────────
// Security fix: any user-generated text (review names/comments, etc.) that
// gets inserted via innerHTML MUST go through this first — otherwise a
// customer name or review comment containing HTML/script would execute
// for every visitor who views it (stored XSS).
function escapeHtml(str) {
    return String(str ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

// ─── API helper ─────────────────────────────────────────────────────────
async function apiFetch(url, options = {}) {
    const res = await fetch(API_BASE + url, {
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        ...options,
    });
    let data = null;
    try { data = await res.json(); } catch (e) { /* no JSON body */ }
    if (!res.ok) {
        const message = (data && data.error) ? data.error : `Request failed (${res.status})`;
        throw new Error(message);
    }
    return data;
}

// Initialize
document.addEventListener('DOMContentLoaded', async function () {
    await loadProducts();
    setupEventListeners();
    setupAuth();
    setupWishlistUI();
    await checkAdminSession();
    await checkCustomerSession(); // Phase 2: loads currentUser + server cart/wishlist if signed in
    loadBanners(); // Phase 5: homepage promo strip

    // CHANGE (Phase 5): if the server rendered this page for a specific
    // product (window.__INITIAL_STATE__.productSlug, set by app.py's
    // serve_product route), boot straight into that product's detail view
    // instead of the homepage — this is what makes a shared/bookmarked
    // /product/<slug> link land on the right saree, not just the homepage.
    const initialSlug = window.__INITIAL_STATE__ && window.__INITIAL_STATE__.productSlug;
    const pathMatch = window.location.pathname.match(/^\/product\/([^/]+)$/);

    if (initialSlug) {
        const product = products.find(p => p.slug === initialSlug);
        if (product) {
            openProduct(product.id, false); // false: URL is already correct, don't push again
        } else {
            navigateToPage('home');
        }
    } else if (pathMatch) {
        // Direct hit on a product URL without server-side state (e.g. dev
        // server restarted mid-session) — resolve it client-side instead.
        const product = products.find(p => p.slug === pathMatch[1]);
        if (product) {
            openProduct(product.id, false);
        } else {
            showNotification("That product couldn't be found.");
            window.history.replaceState(null, '', '/');
            navigateToPage('home');
        }
    } else if (window.location.hash === '#collections') {
        // If redirected from admin.html with #collections, navigate there
        navigateToPage('collections');
        window.history.replaceState(null, '', window.location.pathname);
    } else {
        renderRecentlyViewed('recentlyViewedGridHome', 'recentlyViewedSectionHome'); // Phase 2
        renderHomeFlaggedSections(); // Phase 5
    }
});

// ─── Load products from backend ────────────────────────────────────────
async function loadProducts() {
    try {
        products = await apiFetch('/api/products');
    } catch (err) {
        console.error('Failed to load products:', err);
        products = [];
    }
    renderProducts();
}

// CHANGE (Phase 5): homepage promotional banner strip, admin-managed.
let bannerRotationTimer = null;

async function loadBanners() {
    const strip = document.getElementById('promoBannerStrip');
    if (!strip) return;

    let banners;
    try {
        banners = await apiFetch('/api/banners');
    } catch (err) {
        strip.style.display = 'none';
        return;
    }

    if (bannerRotationTimer) {
        clearInterval(bannerRotationTimer);
        bannerRotationTimer = null;
    }

    if (!banners.length) {
        strip.style.display = 'none';
        return;
    }

    let current = 0;
    const renderBanner = (b) => {
        const inner = `
            <span class="promo-banner-emoji">${b.emoji}</span>
            <span class="promo-banner-title">${b.title}</span>
            ${b.subtitle ? `<span class="promo-banner-subtitle">${b.subtitle}</span>` : ''}
            ${b.linkUrl ? `<a class="promo-banner-link" href="${b.linkUrl}">${b.linkLabel || 'Learn more'} →</a>` : ''}`;
        strip.innerHTML = inner;
    };

    // CHANGE: banner title/subtitle/link come from the admin panel, not
    // customer input, so this mirrors how badges/emoji are already trusted
    // content elsewhere in this codebase (unlike checkout/review fields,
    // which are always escaped).
    renderBanner(banners[current]);
    strip.style.display = 'flex';

    if (banners.length > 1) {
        bannerRotationTimer = setInterval(() => {
            current = (current + 1) % banners.length;
            renderBanner(banners[current]);
        }, 6000);
    }
}

async function checkAdminSession() {
    try {
        const data = await apiFetch('/api/admin/session');
        isAdminLoggedIn = !!data.loggedIn;
    } catch (err) {
        isAdminLoggedIn = false;
    }
}

// Page Navigation
function setupEventListeners() {
    // Navigation links
    document.querySelectorAll('[data-page]').forEach(link => {
        link.addEventListener('click', function (e) {
            e.preventDefault();
            const page = this.getAttribute('data-page');
            navigateToPage(page);
        });
    });

    // Collection cards
    document.querySelectorAll('.collection-card').forEach(card => {
        card.addEventListener('click', function () {
            const collection = this.getAttribute('data-collection');
            if (collection) {
                navigateToPage('collections');
                document.getElementById('categoryFilter').value = collection;
                filterProducts();
            }
        });
    });

    // Cart button
    document.getElementById('cartBtn').addEventListener('click', openCart);
    document.getElementById('closeCart').addEventListener('click', closeCart);

    // Filters
    document.getElementById('categoryFilter')?.addEventListener('change', filterProducts);
    document.getElementById('priceFilter')?.addEventListener('change', filterProducts);
    document.getElementById('sortFilter')?.addEventListener('change', filterProducts);

    // Click outside cart modal to close
    document.getElementById('cartModal').addEventListener('click', function (e) {
        if (e.target === this) closeCart();
    });

    // Click outside product modal to close
    document.getElementById('productModal').addEventListener('click', function (e) {
        if (e.target === this) closeProductModal();
    });

    // Click outside checkout modal to close
    document.getElementById('checkoutModal').addEventListener('click', function (e) {
        if (e.target === this) closeCheckout();
    });

    // CHANGE (Phase 6): mobile nav hamburger — .nav-links has no visible
    // fallback below 768px, so this drawer is the only way to reach
    // Home/Collections/About/Contact/FAQs on a phone.
    const hamburger = document.getElementById('navHamburger');
    const overlay = document.getElementById('mobileNavOverlay');
    const closeBtn = document.getElementById('mobileNavClose');

    function openMobileNav() {
        overlay.classList.add('open');
        hamburger.classList.add('open');
        hamburger.setAttribute('aria-expanded', 'true');
    }
    function closeMobileNav() {
        overlay.classList.remove('open');
        hamburger.classList.remove('open');
        hamburger.setAttribute('aria-expanded', 'false');
    }

    hamburger?.addEventListener('click', () => {
        overlay.classList.contains('open') ? closeMobileNav() : openMobileNav();
    });
    closeBtn?.addEventListener('click', closeMobileNav);
    overlay?.addEventListener('click', (e) => { if (e.target === overlay) closeMobileNav(); });
    // Closing on link click reuses the same [data-page] handler registered
    // above (both navbar and drawer links share the data-page attribute).
    document.querySelectorAll('.mobile-nav-links [data-page]').forEach(link => {
        link.addEventListener('click', closeMobileNav);
    });
}

function navigateToPage(pageName) {
    document.querySelectorAll('.page').forEach(page => {
        page.classList.remove('active');
    });

    const pageMap = {
        'home': 'homePage',
        'collections': 'collectionsPage',
        'about': 'aboutPage',
        'contact': 'contactPage',
        'product': 'productPage',
        'admin': 'adminPage',
        'orderSuccess': 'orderSuccessPage',
        'orderFailed': 'orderFailedPage',
        'faq': 'faqPage',
        'policies': 'policiesPage'
    };

    const pageId = pageMap[pageName];
    if (pageId) {
        // CHANGE: admin page now requires a real authenticated session.
        // If not logged in, send the visitor to admin.html to sign in.
        if (pageName === 'admin' && !isAdminLoggedIn) {
            window.location.href = 'admin.html';
            return;
        }

        document.getElementById(pageId).classList.add('active');
        currentPage = pageName;
        window.scrollTo({ top: 0, behavior: 'smooth' });

        // CHANGE (Phase 5): leaving a /product/<slug> URL for any other page
        // — restore the address bar to a real page URL so it doesn't stay
        // stuck showing a product URL while a different page is visible.
        if (pageName !== 'product' && window.location.pathname.startsWith('/product/')) {
            window.history.pushState(null, '', pageName === 'home' ? '/' : `/#${pageName}`);
        }

        if (pageName === 'admin') {
            refreshAdminTable();
            updateAdminStats();
        }

        // Phase 2: refresh "Recently Viewed" whenever the homepage is shown
        // Phase 5: also refresh the flag-driven Bestsellers/New Arrivals rows
        if (pageName === 'home') {
            renderRecentlyViewed('recentlyViewedGridHome', 'recentlyViewedSectionHome');
            renderHomeFlaggedSections();
        }

        // Phase 5: lightweight page-view tracking (fire-and-forget)
        trackPageView(pageName === 'home' ? '/' : `/${pageName}`);
    }
}

// CHANGE (Phase 5): lightweight, cookie-free page-view tracking for the
// admin Analytics tab. Never blocks or throws — a tracking failure should
// never affect the visitor's experience.
function trackPageView(path, productId = null) {
    apiFetch('/api/track/pageview', {
        method: 'POST',
        body: JSON.stringify({ path, productId }),
    }).catch(() => { /* analytics is best-effort */ });
}

// CHANGE (Phase 5): Bestsellers / New Arrivals homepage rows, driven by the
// featured/bestseller/newArrival flags set in the admin product form.
function renderHomeFlaggedSections() {
    const bestsellers = products.filter(p => p.bestseller).slice(0, 8);
    const newArrivals = products.filter(p => p.newArrival).slice(0, 8);

    const bsSection = document.getElementById('bestsellersSectionHome');
    const bsGrid = document.getElementById('bestsellersGridHome');
    if (bsGrid) {
        bsGrid.innerHTML = bestsellers.map(buildProductCard).join('');
        if (bsSection) bsSection.style.display = bestsellers.length ? '' : 'none';
    }

    const naSection = document.getElementById('newArrivalsSectionHome');
    const naGrid = document.getElementById('newArrivalsGridHome');
    if (naGrid) {
        naGrid.innerHTML = newArrivals.map(buildProductCard).join('');
        if (naSection) naSection.style.display = newArrivals.length ? '' : 'none';
    }
}

// Product Detail Page
// CHANGE (Phase 5): `pushUrl` controls whether we push a new /product/<slug>
// history entry — false when we're arriving here *because* the URL already
// points at this product (initial page load, or Back/Forward navigation).
function openProduct(productId, pushUrl = true) {
    const product = products.find(p => p.id === productId);
    if (!product) return;

    navigateToPage('product');

    document.getElementById('productTitle').textContent = product.name;
    document.getElementById('detailName').textContent = product.name;
    document.getElementById('detailDescription').textContent = product.description || 'Pure Banarasi Silk • Handwoven';
    document.getElementById('detailPrice').textContent = `₹${product.price.toLocaleString('en-IN')}`;

    if (product.imageData) {
        document.getElementById('productImage').innerHTML =
            `<img src="${product.imageData}" alt="${product.name}" style="width:100%;height:100%;object-fit:cover;border-radius:12px;">`;
    } else {
        document.getElementById('productImage').innerHTML = product.emoji;
    }

    document.getElementById('detailCartBtn').onclick = () => addToCart(product.id);
    document.getElementById('buyNowBtn').onclick = () => buyNow(product.id);

    // Phase 2: wishlist toggle on the detail page
    const wishBtn = document.getElementById('detailWishlistBtn');
    wishBtn.onclick = () => toggleWishlist(product.id);
    updateWishlistButton(wishBtn, product.id);

    // Phase 2: reviews for this product
    loadReviews(product.id);

    // Phase 2: related products (same category) + recently viewed
    renderRelatedProducts(product);
    trackRecentlyViewed(product.id);
    renderRecentlyViewed('recentlyViewedGridDetail', 'recentlyViewedSectionDetail', product.id);

    // CHANGE (Phase 5): give this product a real, shareable URL and update
    // the tab title. The server renders the correct meta tags/OG image when
    // this URL is loaded directly or shared — this client-side update keeps
    // the address bar and tab title correct during in-app navigation too.
    if (product.slug) {
        if (pushUrl) window.history.pushState(null, '', `/product/${product.slug}`);
        document.title = `${product.name} — ₹${product.price.toLocaleString('en-IN')} | Shri Jeewani Saree Center`;
    }
    trackPageView(product.slug ? `/product/${product.slug}` : `/product/${product.id}`, product.id);
}

// CHANGE (Phase 5): Back/Forward button support for /product/<slug> URLs.
window.addEventListener('popstate', () => {
    const path = window.location.pathname;
    const match = path.match(/^\/product\/([^/]+)$/);
    if (match) {
        const product = products.find(p => p.slug === match[1]);
        if (product) {
            openProduct(product.id, false);
            return;
        }
    }
    // Anywhere else — just show the homepage without re-pushing history.
    document.querySelectorAll('.page').forEach(page => page.classList.remove('active'));
    document.getElementById('homePage').classList.add('active');
    currentPage = 'home';
    renderRecentlyViewed('recentlyViewedGridHome', 'recentlyViewedSectionHome');
    renderHomeFlaggedSections();
});


// ═══════════════════════════════════════════════════════════════════════
// Related Products (Phase 2)
// Client-side only — computed from the already-loaded `products` array,
// same category as the one being viewed, current product excluded.
// ═══════════════════════════════════════════════════════════════════════
function renderRelatedProducts(product) {
    const section = document.getElementById('relatedSection');
    const grid = document.getElementById('relatedProductsGrid');
    if (!grid) return;

    const related = products
        .filter(p => p.id !== product.id && p.category === product.category)
        .slice(0, 4);

    if (related.length === 0) {
        if (section) section.style.display = 'none';
        return;
    }
    if (section) section.style.display = '';
    grid.innerHTML = related.map(buildProductCard).join('');
}

// ═══════════════════════════════════════════════════════════════════════
// Recently Viewed (Phase 2)
// Stored client-side in localStorage — a personal browsing trail, not tied
// to any account, so it works for guests too.
// ═══════════════════════════════════════════════════════════════════════
const RECENTLY_VIEWED_KEY = 'jeevani_recently_viewed';
const RECENTLY_VIEWED_MAX = 8;

function getRecentlyViewedIds() {
    try {
        const raw = localStorage.getItem(RECENTLY_VIEWED_KEY);
        const ids = raw ? JSON.parse(raw) : [];
        return Array.isArray(ids) ? ids : [];
    } catch (e) {
        return [];
    }
}

function trackRecentlyViewed(productId) {
    let ids = getRecentlyViewedIds().filter(id => id !== productId);
    ids.unshift(productId);
    ids = ids.slice(0, RECENTLY_VIEWED_MAX);
    try {
        localStorage.setItem(RECENTLY_VIEWED_KEY, JSON.stringify(ids));
    } catch (e) { /* storage unavailable — recently viewed just won't persist */ }
}

function renderRecentlyViewed(gridId, sectionId, excludeId = null) {
    const grid = document.getElementById(gridId);
    const section = document.getElementById(sectionId);
    if (!grid) return;

    const ids = getRecentlyViewedIds().filter(id => id !== excludeId);
    const items = ids.map(id => products.find(p => p.id === id)).filter(Boolean);

    if (items.length === 0) {
        if (section) section.style.display = 'none';
        grid.innerHTML = '';
        return;
    }
    if (section) section.style.display = '';
    grid.innerHTML = items.map(buildProductCard).join('');
}

// CHANGE (Phase 2): shared card markup so Related Products and Recently
// Viewed render identically to the main grid without duplicating HTML.
function buildProductCard(product) {
    const imgContent = product.imageData
        ? `<img src="${product.imageData}" alt="${product.name}" style="width:100%;height:100%;object-fit:cover;border-radius:8px 8px 0 0;">`
        : product.emoji;
    const isWishlisted = wishlistIds.has(product.id);
    return `
    <div class="product-card">
        <div class="product-image" onclick="showProduct(${product.id})">
            <button class="wishlist-toggle${isWishlisted ? ' active' : ''}" data-product-id="${product.id}"
                onclick="event.stopPropagation(); toggleWishlist(${product.id})" title="${isWishlisted ? 'Remove from wishlist' : 'Add to wishlist'}">${isWishlisted ? '♥' : '♡'}</button>
            ${imgContent}
            ${product.badge ? `<div class="product-badge">${product.badge}</div>` : ''}
        </div>
        <div class="product-info" onclick="openProduct(${product.id})">
            <h3 class="product-name">${product.name}</h3>
            <p class="product-details">Pure Banarasi Silk • Handwoven</p>
            <p class="product-price">₹${product.price.toLocaleString('en-IN')}</p>
            <button class="add-to-cart" onclick="event.stopPropagation(); addToCart(${product.id})">Add to Cart</button>
            <button class="checkout-btn" onclick="event.stopPropagation(); buyNow(${product.id})">Buy Now</button>
        </div>
    </div>`;
}

function renderProducts(filteredProducts = products) {
    const grid = document.getElementById('productsGrid');
    if (!grid) return;

    if (filteredProducts.length === 0) {
        grid.innerHTML = `<p style="text-align:center; padding:3rem 0; grid-column:1/-1; color: var(--stone-soft);">No sarees match your filters right now.</p>`;
        return;
    }

    grid.innerHTML = filteredProducts.map(buildProductCard).join('');
}

// Filter Products
// CHANGE: filtering/sorting now delegated to the backend (/api/products),
// keeping the same dropdown IDs/values and behavior the UI already has.
async function filterProducts() {
    const category = document.getElementById('categoryFilter').value;
    const priceRange = document.getElementById('priceFilter').value;
    const sortBy = document.getElementById('sortFilter').value;

    const params = new URLSearchParams({ category, price: priceRange, sort: sortBy });

    try {
        const filtered = await apiFetch(`/api/products?${params.toString()}`);
        renderProducts(filtered);
    } catch (err) {
        console.error('Failed to filter products:', err);
    }
}

// ─────────────────────────────────────────────────────────────────────────
// Cart Functions
// CHANGE (Phase 2): when a customer is signed in, the cart is persisted on
// the server (/api/cart) so it survives across devices/sessions. Guests
// keep the original in-memory behavior so checkout still works without an
// account, exactly as before.
// ─────────────────────────────────────────────────────────────────────────
function addToCart(productId) {
    const product = products.find(p => p.id === productId);
    if (!product) return;

    if (currentUser) {
        apiFetch('/api/cart', {
            method: 'POST',
            body: JSON.stringify({ productId, quantity: 1 }),
        }).then(items => {
            cart = items;
            updateCartCount();
            if (document.getElementById('cartModal').classList.contains('active')) renderCart();
        }).catch(err => {
            showNotification('Could not add to cart: ' + err.message);
        });
        showNotification('Added to cart!');
        return;
    }

    const existingItem = cart.find(item => item.id === productId);
    if (existingItem) {
        existingItem.quantity += 1;
    } else {
        cart.push({ ...product, quantity: 1 });
    }

    updateCartCount();
    showNotification('Added to cart!');
}

function updateCartCount() {
    const count = cart.reduce((sum, item) => sum + item.quantity, 0);
    document.getElementById('cartCount').textContent = count;
}

function openCart() {
    renderCart();
    document.getElementById('cartModal').classList.add('active');
}

function closeCart() {
    document.getElementById('cartModal').classList.remove('active');
}

function renderCart() {
    const cartItemsDiv = document.getElementById('cartItems');

    if (cart.length === 0) {
        cartItemsDiv.innerHTML = `
            <div class="empty-cart">
                <div class="empty-cart-icon">🛒</div>
                <p>Your cart is empty</p>
                <p>Explore our beautiful collections!</p>
            </div>
        `;
        return;
    }

    const subtotal = cart.reduce((sum, item) => sum + (item.price * item.quantity), 0);
    const tax = subtotal * 0.05;
    const shipping = 0;
    const total = subtotal + tax + shipping;

    cartItemsDiv.innerHTML = `
        ${cart.map(item => `
            <div class="cart-item">
                <div class="cart-item-image">${item.imageData ? `<img src="${item.imageData}" alt="${item.name}" style="width:100%;height:100%;object-fit:cover;">` : item.emoji}</div>
                <div class="cart-item-details">
                    <h3 class="cart-item-name">${item.name}</h3>
                    <p class="cart-item-price">₹${item.price.toLocaleString('en-IN')}</p>
                    <div class="quantity-controls">
                        <button class="qty-btn" onclick="updateQuantity(${item.id}, -1)">-</button>
                        <span class="quantity">${item.quantity}</span>
                        <button class="qty-btn" onclick="updateQuantity(${item.id}, 1)">+</button>
                        <button class="remove-item" onclick="removeFromCart(${item.id})">Remove</button>
                    </div>
                </div>
            </div>
        `).join('')}

        <div class="cart-summary">
            <div class="summary-row">
                <span>Subtotal:</span>
                <span>₹${subtotal.toLocaleString('en-IN')}</span>
            </div>
            <div class="summary-row">
                <span>Tax (5%):</span>
                <span>₹${tax.toLocaleString('en-IN')}</span>
            </div>
            <div class="summary-row">
                <span>Shipping:</span>
                <span>${shipping === 0 ? 'FREE' : '₹' + shipping.toLocaleString('en-IN')}</span>
            </div>
            <div class="summary-row summary-total">
                <span>Total:</span>
                <span>₹${total.toLocaleString('en-IN')}</span>
            </div>
            <button class="checkout-btn" onclick="openCheckout()">PROCEED TO CHECKOUT</button>
        </div>
    `;
}

function updateQuantity(productId, change) {
    const item = cart.find(i => i.id === productId);
    if (!item) return;

    const newQty = item.quantity + change;

    if (currentUser) {
        if (newQty <= 0) {
            removeFromCart(productId);
            return;
        }
        apiFetch(`/api/cart/${productId}`, {
            method: 'PUT',
            body: JSON.stringify({ quantity: newQty }),
        }).then(items => {
            cart = items;
            updateCartCount();
            renderCart();
        }).catch(err => showNotification('Could not update cart: ' + err.message));
        return;
    }

    item.quantity = newQty;
    if (item.quantity <= 0) {
        removeFromCart(productId);
    } else {
        updateCartCount();
        renderCart();
    }
}

function removeFromCart(productId) {
    if (currentUser) {
        apiFetch(`/api/cart/${productId}`, { method: 'DELETE' })
            .then(items => {
                cart = items;
                updateCartCount();
                renderCart();
            })
            .catch(err => showNotification('Could not remove item: ' + err.message));
        return;
    }

    cart = cart.filter(item => item.id !== productId);
    updateCartCount();
    renderCart();
}

function showNotification(message) {
    const notification = document.createElement('div');
    notification.textContent = message;
    notification.style.cssText = `
        position: fixed;
        bottom: 30px;
        right: 30px;
        background: var(--maroon);
        color: white;
        padding: 1rem 2rem;
        border-radius: 5px;
        font-family: 'Playfair Display', serif;
        font-size: 1.1rem;
        z-index: 3000;
        animation: slideIn 0.3s ease;
    `;
    document.body.appendChild(notification);

    setTimeout(() => {
        notification.style.opacity = '0';
        notification.style.transition = 'opacity 0.3s ease';
        setTimeout(() => notification.remove(), 300);
    }, 2000);
}

// ─────────────────────────────────────────────────────────────────────────
// CHANGE (Phase 6): Contact form — now submits to our own backend
// (/api/contact) instead of a third-party form service. The message is
// stored (visible in the admin Messages tab) and emailed to the store.
// ─────────────────────────────────────────────────────────────────────────
async function submitContactForm(event) {
    event.preventDefault();
    const errorBox = document.getElementById('contactFormError');
    const successBox = document.getElementById('contactFormSuccess');
    const submitBtn = document.getElementById('contactFormSubmit');
    errorBox.textContent = '';
    successBox.style.display = 'none';

    const payload = {
        name: document.getElementById('contactName').value.trim(),
        email: document.getElementById('contactEmail').value.trim(),
        phone: document.getElementById('contactPhone').value.trim(),
        subject: document.getElementById('contactSubject').value.trim(),
        message: document.getElementById('contactMessage').value.trim(),
    };

    if (!payload.name || !payload.email || !payload.message) {
        errorBox.textContent = 'Please fill in your name, email, and message.';
        return;
    }

    submitBtn.disabled = true;
    submitBtn.textContent = 'Sending…';

    try {
        await apiFetch('/api/contact', { method: 'POST', body: JSON.stringify(payload) });
        document.getElementById('contactForm').reset();
        successBox.style.display = 'block';
    } catch (err) {
        errorBox.textContent = err.message || 'Could not send your message. Please try again.';
    } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = 'Send Message';
    }
}

// ─────────────────────────────────────────────────────────────────────────
// CHANGE (Phase 6): Newsletter signup band (shown on every page)
// ─────────────────────────────────────────────────────────────────────────
async function submitNewsletterForm(event) {
    event.preventDefault();
    const emailInput = document.getElementById('newsletterEmail');
    const msgBox = document.getElementById('newsletterMsg');
    const submitBtn = document.getElementById('newsletterSubmit');
    const email = emailInput.value.trim();

    msgBox.style.display = 'none';
    if (!email) return;

    submitBtn.disabled = true;
    submitBtn.textContent = 'Subscribing…';

    try {
        const result = await apiFetch('/api/newsletter/subscribe', { method: 'POST', body: JSON.stringify({ email }) });
        msgBox.textContent = result.alreadySubscribed
            ? "You're already subscribed — thank you!"
            : "You're subscribed! Check your inbox for a welcome email.";
        msgBox.style.display = 'block';
        emailInput.value = '';
    } catch (err) {
        msgBox.textContent = err.message || 'Could not subscribe right now. Please try again.';
        msgBox.style.display = 'block';
    } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = 'Subscribe';
    }
}

// ─────────────────────────────────────────────────────────────────────────
// CHANGE (Phase 6): FAQ accordion (faqPage)
// ─────────────────────────────────────────────────────────────────────────
function toggleFaq(btn) {
    const item = btn.closest('.faq-item');
    const wasOpen = item.classList.contains('open');
    // Accordion behavior: opening one closes the others in the same list.
    item.parentElement.querySelectorAll('.faq-item.open').forEach(i => i.classList.remove('open'));
    if (!wasOpen) item.classList.add('open');
}

// ─────────────────────────────────────────────────────────────────────────
// CHANGE (Phase 6): Policies page (policiesPage) — one page, multiple
// anchored sections switched via a left-hand nav.
// ─────────────────────────────────────────────────────────────────────────
function showPolicySection(event, section) {
    if (event) event.preventDefault();
    document.querySelectorAll('.policy-section').forEach(s => s.classList.remove('active'));
    document.querySelectorAll('.policies-nav a').forEach(a => a.classList.remove('active'));
    document.querySelector(`.policy-section[data-policy-section="${section}"]`)?.classList.add('active');
    document.querySelector(`.policies-nav a[data-policy-nav="${section}"]`)?.classList.add('active');
    document.querySelector('.policies-content')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// Entry point used by footer/FAQ links: navigates to the Policies page AND
// selects the right section, in one call.
function openPolicySection(event, section) {
    if (event) event.preventDefault();
    navigateToPage('policies');
    showPolicySection(null, section);
}

// Product Quick-View Popup (image click)
function showProduct(id) {
    const product = products.find(p => p.id === id);
    if (!product) return;

    document.getElementById('modalProductName').innerText = product.name;
    document.getElementById('modalProductImage').innerHTML = product.imageData
        ? `<img src="${product.imageData}" alt="${product.name}" style="max-width:100%;max-height:220px;border-radius:8px;object-fit:contain;">`
        : product.emoji;
    document.getElementById('modalProductDesc').innerText = product.description || 'Pure Banarasi Silk • Handwoven';
    document.getElementById('modalProductPrice').innerText = '₹' + product.price.toLocaleString('en-IN');
    document.getElementById('modalAddCart').onclick = () => addToCart(product.id);

    // Phase 2: wishlist toggle in the quick-view modal
    const wishBtn = document.getElementById('modalWishlistBtn');
    wishBtn.onclick = () => toggleWishlist(product.id);
    updateWishlistButton(wishBtn, product.id);

    document.getElementById('productModal').classList.add('active');
}

function closeProductModal() {
    document.getElementById('productModal').classList.remove('active');
}

// Checkout Modal
// CHANGE (Phase 2/3): checkout now requires a signed-in account (orders are
// tied to a customer record), supports an optional coupon code, and
// placeOrder() persists a real order via /api/orders (COD) or the Razorpay
// checkout widget (UPI/Card) instead of just showing a local alert.
function computeCartTotals() {
    const subtotal = cart.reduce((sum, item) => sum + item.price * item.quantity, 0);
    const tax = Math.round(subtotal * 0.05);
    const paymentEl = document.querySelector('input[name="payment"]:checked');
    const paymentMethod = paymentEl ? paymentEl.value : 'cod';

    // CHANGE: COD carries a small handling surcharge; UPI/Card get an
    // incentive discount. Mirrors build_order_from_cart() in app.py exactly
    // so this preview always matches what /api/orders actually charges.
    let shipping = paymentMethod === 'cod' ? 50 : 0;

    const couponDiscount = appliedCoupon ? Math.min(appliedCoupon.discount, subtotal) : 0;
    const onlineDiscount = (paymentMethod === 'upi' || paymentMethod === 'card') ? Math.round(subtotal * 0.10) : 0;
    const discount = couponDiscount + onlineDiscount;
    const total = Math.max(subtotal + tax + shipping - discount, 0);
    return { subtotal, tax, shipping, couponDiscount, onlineDiscount, discount, total, paymentMethod };
}

function openCheckout() {
    if (!currentUser) {
        closeCart();
        document.getElementById('loginModal').classList.add('active');
        renderAuthState();
        showAuthError('Please sign in to place an order.');
        return;
    }

    appliedCoupon = null;
    document.getElementById('couponCodeInput').value = '';
    document.getElementById('couponMessage').textContent = '';
    document.getElementById('couponMessage').className = 'coupon-message';
    document.getElementById('checkoutError').textContent = '';
    renderCouponUI();
    renderOrderSummary();

    // Bug fix: make sure the button isn't left disabled/relabeled from a
    // previous attempt (e.g. the customer completed a Razorpay payment,
    // came back, and opened checkout again for a new order).
    const placeBtn = document.getElementById('placeOrderBtn');
    if (placeBtn) { placeBtn.disabled = false; placeBtn.textContent = 'Place Order'; }

    closeCart();
    document.getElementById('checkoutModal').classList.add('active');

    // Phase 2: prefill shipping details for signed-in customers
    const nameField = document.getElementById('customerName');
    const phoneField = document.getElementById('customerPhone');
    if (nameField && !nameField.value) nameField.value = currentUser.name;
    if (phoneField && !phoneField.value) phoneField.value = currentUser.phone || '';
}

function renderOrderSummary() {
    const { subtotal, tax, shipping, couponDiscount, onlineDiscount, total, paymentMethod } = computeCartTotals();
    document.getElementById('orderSummary').innerHTML = `
        <h3>Order Summary</h3>
        <div class="order-summary-row"><span>Total Items:</span><span>${cart.reduce((s, i) => s + i.quantity, 0)}</span></div>
        <div class="order-summary-row"><span>Subtotal:</span><span>₹${subtotal.toLocaleString('en-IN')}</span></div>
        <div class="order-summary-row"><span>Tax (5%):</span><span>₹${tax.toLocaleString('en-IN')}</span></div>
        <div class="order-summary-row"><span>Shipping${paymentMethod === 'cod' ? ' (incl. ₹50 COD fee)' : ''}:</span><span>${shipping === 0 ? 'FREE' : '₹' + shipping.toLocaleString('en-IN')}</span></div>
        ${onlineDiscount > 0 ? `<div class="order-summary-row discount"><span>Online Payment Discount (10%):</span><span>-₹${onlineDiscount.toLocaleString('en-IN')}</span></div>` : ''}
        ${couponDiscount > 0 ? `<div class="order-summary-row discount"><span>Discount (${appliedCoupon.code}):</span><span>-₹${couponDiscount.toLocaleString('en-IN')}</span></div>` : ''}
        <div class="order-summary-row total"><span>Total:</span><span>₹${total.toLocaleString('en-IN')}</span></div>
    `;
}

// Phase 2: coupon apply/remove
function renderCouponUI() {
    const appliedRow = document.getElementById('couponAppliedRow');
    const inputRow = document.getElementById('couponInputRow');
    if (appliedCoupon) {
        appliedRow.innerHTML = `<div class="coupon-applied-pill">✓ ${appliedCoupon.code} applied <button type="button" onclick="removeCoupon()">✕</button></div>`;
        inputRow.style.display = 'none';
    } else {
        appliedRow.innerHTML = '';
        inputRow.style.display = 'flex';
    }
}

async function applyCoupon() {
    const code = document.getElementById('couponCodeInput').value.trim();
    const msgEl = document.getElementById('couponMessage');
    if (!code) return;

    msgEl.textContent = 'Checking...';
    msgEl.className = 'coupon-message';

    try {
        const data = await apiFetch('/api/coupons/validate', {
            method: 'POST',
            body: JSON.stringify({ code }),
        });
        appliedCoupon = data;
        msgEl.textContent = `Coupon applied! You saved ₹${data.discount.toLocaleString('en-IN')}.`;
        msgEl.className = 'coupon-message success';
    } catch (err) {
        appliedCoupon = null;
        msgEl.textContent = err.message;
        msgEl.className = 'coupon-message error';
    }
    renderCouponUI();
    renderOrderSummary();
}

function removeCoupon() {
    appliedCoupon = null;
    document.getElementById('couponCodeInput').value = '';
    document.getElementById('couponMessage').textContent = '';
    document.getElementById('couponMessage').className = 'coupon-message';
    renderCouponUI();
    renderOrderSummary();
}

function closeCheckout() {
    document.getElementById('checkoutModal').classList.remove('active');
}

// Buy Now
function buyNow(productId) {
    if (!currentUser) {
        document.getElementById('loginModal').classList.add('active');
        renderAuthState();
        showAuthError('Please sign in to buy this item.');
        return;
    }
    addToCart(productId);
    openCheckout();
}

// Place Order
// CHANGE (Phase 2/3): real persistence. COD orders are created directly;
// UPI/Card orders go through the Razorpay checkout widget and are only
// finalized once payment is verified server-side.
function placeOrder() {
    const name    = document.getElementById('customerName').value.trim();
    const phone   = document.getElementById('customerPhone').value.trim();
    const address = document.getElementById('customerAddress').value.trim();
    const pincode = document.getElementById('customerPincode').value.trim();
    const paymentMethod = document.querySelector('input[name="payment"]:checked').value;
    const errEl = document.getElementById('checkoutError');
    errEl.textContent = '';

    if (!name || !phone || !address || !pincode) {
        errEl.textContent = 'Please fill all details';
        return;
    }

    const payload = {
        name, phone, address, pincode,
        paymentMethod,
        couponCode: appliedCoupon ? appliedCoupon.code : '',
    };

    const placeBtn = document.getElementById('placeOrderBtn');
    placeBtn.disabled = true;
    placeBtn.textContent = 'Placing Order...';

    const resetButton = () => {
        placeBtn.disabled = false;
        placeBtn.textContent = 'Place Order';
    };

    if (paymentMethod === 'cod') {
        apiFetch('/api/orders', { method: 'POST', body: JSON.stringify(payload) })
            .then(order => onOrderPlaced(order))
            .catch(err => { errEl.textContent = err.message; resetButton(); });
        return;
    }

    // UPI / Card via Razorpay
    apiFetch('/api/orders/razorpay/create', { method: 'POST', body: JSON.stringify(payload) })
        .then(rpData => {
            if (typeof Razorpay === 'undefined') {
                apiFetch('/api/orders/razorpay/cancel', { method: 'POST', body: JSON.stringify({ orderNumber: rpData.orderNumber }) }).catch(() => {});
                goToOrderFailed('Payment widget failed to load. Please check your connection and try again.');
                resetButton();
                return;
            }

            const rzp = new Razorpay({
                key: rpData.keyId,
                amount: rpData.amount,
                currency: rpData.currency,
                order_id: rpData.razorpayOrderId,
                name: 'Shri Jeewani Saree Center',
                description: `Order ${rpData.orderNumber}`,
                prefill: { name, contact: phone, email: currentUser.email || '' },
                theme: { color: '#0F5C56' },
                method: paymentMethod === 'upi' ? { upi: true, card: false, netbanking: false, wallet: false } : { card: true, upi: false, netbanking: false, wallet: false },
                handler: function (response) {
                    apiFetch('/api/orders/razorpay/verify', {
                        method: 'POST',
                        body: JSON.stringify({
                            orderNumber: rpData.orderNumber,
                            razorpayOrderId: response.razorpay_order_id,
                            razorpayPaymentId: response.razorpay_payment_id,
                            razorpaySignature: response.razorpay_signature,
                        }),
                    })
                        .then(order => onOrderPlaced(order))
                        .catch(err => { goToOrderFailed('Payment succeeded but verification failed: ' + err.message); resetButton(); });
                },
                modal: {
                    ondismiss: function () {
                        apiFetch('/api/orders/razorpay/cancel', { method: 'POST', body: JSON.stringify({ orderNumber: rpData.orderNumber }) }).catch(() => {});
                        resetButton();
                        // No navigation here — the customer just closed the widget and is
                        // still on the checkout modal, free to try again immediately.
                        errEl.textContent = 'Payment cancelled.';
                    },
                },
            });
            rzp.open();
            // Bug fix: do NOT reset the button here. The widget is now open
            // and payment is in progress — re-enabling "Place Order" at this
            // point let a customer click it again and spin up a second
            // pending order (decrementing stock again) while the first
            // payment was still being completed. The button is correctly
            // reset in modal.ondismiss and in the handler's .catch below,
            // which cover every way this flow can end.
        })
        .catch(err => { errEl.textContent = err.message; resetButton(); });
}

function onOrderPlaced(order) {
    cart = [];
    appliedCoupon = null;
    updateCartCount();
    closeCheckout();

    document.getElementById('customerName').value = '';
    document.getElementById('customerPhone').value = '';
    document.getElementById('customerAddress').value = '';
    document.getElementById('customerPincode').value = '';

    loadMyOrders();
    loadProducts(); // stock levels changed

    // Phase 3: show the dedicated order confirmation page instead of an alert
    navigateToPage('orderSuccess');
    const itemsSummary = order.items.map(i => `${i.name} × ${i.quantity}`).join(', ');
    document.getElementById('orderSuccessDetails').innerHTML = `
        <div class="order-summary-row"><span>Order Number:</span><span>${order.orderNumber}</span></div>
        <div class="order-summary-row"><span>Items:</span><span>${itemsSummary}</span></div>
        <div class="order-summary-row"><span>Payment:</span><span>${order.paymentMethod === 'cod' ? 'Cash on Delivery' : order.paymentMethod.toUpperCase()}</span></div>
        <div class="order-summary-row total"><span>Total Paid:</span><span>₹${order.total.toLocaleString('en-IN')}</span></div>
    `;
}

// Phase 3: dedicated failure page for payment problems the customer can't
// just fix inline (widget failed to load, verification failed after the
// fact). A simple cancel/dismiss stays on the checkout modal instead — see
// modal.ondismiss above.
function goToOrderFailed(message) {
    document.getElementById('orderFailedMessage').textContent = message || 'Your payment could not be completed. No amount has been charged, and your cart is safe.';
    navigateToPage('orderFailed');
}

async function retryCheckoutAfterFailure() {
    // The server restores cart items when a Razorpay payment fails or is
    // cancelled, but the in-memory `cart` here is stale until we reload it.
    await loadServerCart();
    navigateToPage('collections');
    openCheckout();
}

// ═══════════════════════════════════════════════════════════════════════
// Customer Accounts
// CHANGE (Phase 2): replaced the in-memory `registeredUsers` mock with
// real, server-backed accounts via /api/auth/*. The login/signup modal,
// its element IDs, and its layout are unchanged — only the logic behind
// the forms now talks to the database (the `users` table created in
// Phase 1's schema.sql).
// ═══════════════════════════════════════════════════════════════════════

let currentUser = null;

async function checkCustomerSession() {
    try {
        const data = await apiFetch('/api/auth/session');
        if (data.loggedIn) {
            currentUser = data.user;
            await loadServerCart();
            await loadServerWishlist();
        }
    } catch (err) {
        currentUser = null;
    }
    renderAuthState();
}

async function loadServerCart() {
    try {
        cart = await apiFetch('/api/cart');
    } catch (err) {
        console.error('Failed to load cart:', err);
    }
    updateCartCount();
}

function setupAuth() {
    const accountBtn = document.getElementById('accountBtn');
    const loginModal = document.getElementById('loginModal');
    const closeLogin = document.getElementById('closeLogin');
    const tabSignIn = document.getElementById('tabSignIn');
    const tabSignUp = document.getElementById('tabSignUp');
    const signInForm = document.getElementById('signInForm');
    const signUpForm = document.getElementById('signUpForm');

    accountBtn.addEventListener('click', () => {
        loginModal.classList.add('active');
        renderAuthState();
    });

    closeLogin.addEventListener('click', () => loginModal.classList.remove('active'));

    loginModal.addEventListener('click', function (e) {
        if (e.target === this) loginModal.classList.remove('active');
    });

    tabSignIn.addEventListener('click', () => switchAuthTab('signin'));
    tabSignUp.addEventListener('click', () => switchAuthTab('signup'));

    document.getElementById('switchToSignUp').addEventListener('click', (e) => {
        e.preventDefault();
        switchAuthTab('signup');
    });

    document.getElementById('switchToSignIn').addEventListener('click', (e) => {
        e.preventDefault();
        switchAuthTab('signin');
    });

    // CHANGE (Phase 4): real forgot/reset password flow, backed by email.
    document.getElementById('forgotPasswordLink').addEventListener('click', (e) => {
        e.preventDefault();
        switchAuthTab('forgot');
    });

    document.getElementById('backToSignIn').addEventListener('click', (e) => {
        e.preventDefault();
        switchAuthTab('signin');
    });

    document.getElementById('forgotPasswordForm').addEventListener('submit', async function (e) {
        e.preventDefault();
        clearAuthError();

        const email = document.getElementById('forgotEmail').value.trim();
        const submitBtn = this.querySelector('.auth-submit');
        submitBtn.disabled = true;
        submitBtn.textContent = 'Sending...';

        try {
            const data = await apiFetch('/api/auth/forgot-password', {
                method: 'POST',
                body: JSON.stringify({ email }),
            });
            showNotification(data.message || 'If an account exists with that email, a reset link has been sent.');
            switchAuthTab('signin');
        } catch (err) {
            showAuthError(err.message);
        } finally {
            submitBtn.disabled = false;
            submitBtn.textContent = 'Send Reset Link';
        }
    });

    document.getElementById('resetPasswordForm').addEventListener('submit', async function (e) {
        e.preventDefault();
        clearAuthError();

        const newPassword = document.getElementById('resetNewPassword').value;
        if (newPassword.length < 6) {
            showAuthError('Please choose a password that is at least 6 characters long.');
            return;
        }

        const submitBtn = this.querySelector('.auth-submit');
        submitBtn.disabled = true;
        submitBtn.textContent = 'Resetting...';

        try {
            await apiFetch('/api/auth/reset-password', {
                method: 'POST',
                body: JSON.stringify({ token: pendingResetToken, newPassword }),
            });
            showNotification('Your password has been reset. Please sign in.');
            pendingResetToken = null;
            // Clean the token out of the URL so a page refresh doesn't re-show the reset form.
            const url = new URL(window.location);
            url.searchParams.delete('resetToken');
            window.history.replaceState({}, '', url);
            switchAuthTab('signin');
        } catch (err) {
            showAuthError(err.message);
        } finally {
            submitBtn.disabled = false;
            submitBtn.textContent = 'Reset Password';
        }
    });

    // CHANGE (Phase 4): a password-reset email link lands here with
    // ?resetToken=... — open the modal straight to the reset form.
    const urlParams = new URLSearchParams(window.location.search);
    const tokenFromUrl = urlParams.get('resetToken');
    if (tokenFromUrl) {
        pendingResetToken = tokenFromUrl;
        loginModal.classList.add('active');
        switchAuthTab('reset');
    }

    signInForm.addEventListener('submit', async function (e) {
        e.preventDefault();
        clearAuthError();

        const identifier = document.getElementById('signInEmail').value.trim();
        const password = document.getElementById('signInPassword').value;

        const submitBtn = this.querySelector('.auth-submit');
        submitBtn.disabled = true;
        submitBtn.textContent = 'Signing In...';

        let data;
        try {
            data = await apiFetch('/api/auth/login', {
                method: 'POST',
                body: JSON.stringify({ identifier, password }),
            });
        } catch (err) {
            showAuthError(err.message);
            return;
        } finally {
            submitBtn.disabled = false;
            submitBtn.textContent = 'Sign In';
        }

        // CHANGE: a correct password no longer signs the customer in by
        // itself — a 6-digit code has been emailed and must be confirmed.
        if (data.otpRequired) {
            pendingLoginIdentifier = identifier;
            pendingLoginOtpToken = data.otpToken || null;
            document.getElementById('otpHintText').textContent = data.message || 'Enter the 6-digit code we emailed you.';
            document.getElementById('loginOtpCode').value = '';
            switchAuthTab('otp');
            return;
        }

        // Fallback path (accounts with no email on file skip the OTP step
        // server-side and log in immediately).
        await onAuthSuccess(data.user);
        showNotification(`Welcome back, ${data.user.name.split(' ')[0]}!`);
    });

    // CHANGE: Login OTP form — the second step of every sign-in.
    document.getElementById('loginOtpForm').addEventListener('submit', async function (e) {
        e.preventDefault();
        clearAuthError();

        // Safari fix: strip any non-digits, handle spaces/dashes gracefully
        let otp = document.getElementById('loginOtpCode').value.trim().replace(/\D/g, '');
        if (otp.length !== 6) {
            showAuthError('Please enter the 6-digit code.');
            return;
        }

        const submitBtn = this.querySelector('.auth-submit');
        submitBtn.disabled = true;
        submitBtn.textContent = 'Verifying...';

        let data;
        try {
            data = await apiFetch('/api/auth/login/verify-otp', {
                method: 'POST',
                body: JSON.stringify({ identifier: pendingLoginIdentifier, otp, otpToken: pendingLoginOtpToken }),
            });
        } catch (err) {
            showAuthError(err.message);
            return;
        } finally {
            submitBtn.disabled = false;
            submitBtn.textContent = 'Verify & Sign In';
        }

        pendingLoginIdentifier = null;
        pendingLoginOtpToken = null;
        await onAuthSuccess(data.user);
        showNotification(`Welcome back, ${data.user.name.split(' ')[0]}!`);
    });

    document.getElementById('resendOtpLink').addEventListener('click', async (e) => {
        e.preventDefault();
        clearAuthError();
        if (!pendingLoginIdentifier) return;

        const link = e.target;
        const originalText = link.textContent;
        link.textContent = 'Sending...';

        try {
            const data = await apiFetch('/api/auth/login/resend-otp', {
                method: 'POST',
                body: JSON.stringify({ identifier: pendingLoginIdentifier, otpToken: pendingLoginOtpToken }),
            });
            if (data.otpToken) {
                pendingLoginOtpToken = data.otpToken;
            }
            showNotification(data.message || 'A new code has been sent.');
        } catch (err) {
            showAuthError(err.message);
        } finally {
            link.textContent = originalText;
        }
    });

    document.getElementById('backToSignInFromOtp').addEventListener('click', (e) => {
        e.preventDefault();
        pendingLoginIdentifier = null;
        pendingLoginOtpToken = null;
        switchAuthTab('signin');
    });

    // CHANGE: OTP input filter for Safari compatibility — strip non-digits in real time
    // so the user can't accidentally type letters/dashes. This works cross-browser.
    document.getElementById('loginOtpCode').addEventListener('input', function (e) {
        this.value = this.value.replace(/\D/g, '').slice(0, 6);
    });

    document.getElementById('signupOtpCode').addEventListener('input', function (e) {
        this.value = this.value.replace(/\D/g, '').slice(0, 6);
    });

    signUpForm.addEventListener('submit', async function (e) {
        e.preventDefault();
        clearAuthError();

        const name = document.getElementById('signUpName').value.trim();
        const email = document.getElementById('signUpEmail').value.trim();
        const phone = document.getElementById('signUpPhone').value.trim();
        const password = document.getElementById('signUpPassword').value;

        if (!name || !email || !phone || !password) {
            showAuthError('Please fill in all fields to create your account.');
            return;
        }

        if (password.length < 6) {
            showAuthError('Please choose a password that is at least 6 characters long.');
            return;
        }

        const submitBtn = this.querySelector('.auth-submit');
        submitBtn.disabled = true;
        submitBtn.textContent = 'Creating Account...';

        let data;
        try {
            data = await apiFetch('/api/auth/signup', {
                method: 'POST',
                body: JSON.stringify({ name, email, phone, password }),
            });
        } catch (err) {
            showAuthError(err.message);
            if (/already exists/i.test(err.message)) switchAuthTab('signin');
            return;
        } finally {
            submitBtn.disabled = false;
            submitBtn.textContent = 'Create Account';
        }

        // CHANGE: account is created but needs OTP email verification before
        // the customer can sign in. Same flow as login OTP.
        if (data.otpRequired) {
            pendingSignupEmail = email;
            pendingSignupOtpToken = data.otpToken || null;
            document.getElementById('signupOtpHintText').textContent = data.message || 'Enter the 6-digit code we emailed you.';
            document.getElementById('signupOtpCode').value = '';
            switchAuthTab('signup-otp');
            return;
        }

        // Fallback (should not happen in normal flow).
        await onAuthSuccess(data.user);
        showNotification(`Account created. Welcome, ${data.user.name.split(' ')[0]}!`);
    });

    // CHANGE: Signup OTP form — email verification for new accounts.
    document.getElementById('signupOtpForm').addEventListener('submit', async function (e) {
        e.preventDefault();
        clearAuthError();

        let otp = document.getElementById('signupOtpCode').value.trim().replace(/\D/g, '');
        if (otp.length !== 6) {
            showAuthError('Please enter the 6-digit code.');
            return;
        }

        const submitBtn = this.querySelector('.auth-submit');
        submitBtn.disabled = true;
        submitBtn.textContent = 'Verifying...';

        let data;
        try {
            data = await apiFetch('/api/auth/signup/verify-otp', {
                method: 'POST',
                body: JSON.stringify({ email: pendingSignupEmail, otp, otpToken: pendingSignupOtpToken }),
            });
        } catch (err) {
            showAuthError(err.message);
            return;
        } finally {
            submitBtn.disabled = false;
            submitBtn.textContent = 'Verify & Activate Account';
        }

        pendingSignupEmail = null;
        pendingSignupOtpToken = null;
        await onAuthSuccess(data.user);
        showNotification(`Account activated. Welcome, ${data.user.name.split(' ')[0]}!`);
    });

    document.getElementById('resendSignupOtpLink').addEventListener('click', async (e) => {
        e.preventDefault();
        clearAuthError();
        if (!pendingSignupEmail) return;

        const link = e.target;
        const originalText = link.textContent;
        link.textContent = 'Sending...';

        try {
            const data = await apiFetch('/api/auth/signup/resend-otp', {
                method: 'POST',
                body: JSON.stringify({ email: pendingSignupEmail, otpToken: pendingSignupOtpToken }),
            });
            if (data.otpToken) {
                pendingSignupOtpToken = data.otpToken;
            }
            showNotification(data.message || 'A new code has been sent.');
        } catch (err) {
            showAuthError(err.message);
        } finally {
            link.textContent = originalText;
        }
    });

    document.getElementById('backToSignUpFromOtp').addEventListener('click', (e) => {
        e.preventDefault();
        pendingSignupEmail = null;
        pendingSignupOtpToken = null;
        switchAuthTab('signup');
    });

    document.getElementById('signOutBtn').addEventListener('click', async () => {
        try {
            await apiFetch('/api/auth/logout', { method: 'POST' });
        } catch (err) { /* ignore */ }

        currentUser = null;
        cart = [];
        wishlist = [];
        wishlistIds = new Set();
        myOrdersCache = [];
        updateCartCount();
        updateWishlistCount();
        renderProducts();
        switchAuthTab('signin');
        renderAuthState();
        showNotification('You have been signed out.');
    });
}

// Phase 2: shared post-login/signup flow — merges any guest cart into the
// account, then loads the account's real cart + wishlist from the server.
async function onAuthSuccess(user) {
    currentUser = user;

    const guestItems = cart.map(item => ({ productId: item.id, quantity: item.quantity }));
    try {
        if (guestItems.length > 0) {
            cart = await apiFetch('/api/cart/merge', {
                method: 'POST',
                body: JSON.stringify({ items: guestItems }),
            });
        } else {
            cart = await apiFetch('/api/cart');
        }
    } catch (err) {
        console.error('Failed to sync cart:', err);
    }
    updateCartCount();

    await loadServerWishlist();
    renderProducts();
    renderAuthState();
}

function switchAuthTab(which) {
    const tabs = document.querySelector('.auth-tabs');
    const tabSignIn = document.getElementById('tabSignIn');
    const tabSignUp = document.getElementById('tabSignUp');
    const signInForm = document.getElementById('signInForm');
    const signUpForm = document.getElementById('signUpForm');
    const forgotForm = document.getElementById('forgotPasswordForm');
    const resetForm = document.getElementById('resetPasswordForm');
    const otpForm = document.getElementById('loginOtpForm');
    const signupOtpForm = document.getElementById('signupOtpForm');

    clearAuthError();
    document.getElementById('authSuccess').classList.add('hidden');

    // CHANGE (Phase 4): 'forgot' and 'reset' are standalone panels — no tabs,
    // just the one form plus a way back to Sign In. CHANGE: 'otp' (the
    // second step of every login) works the same way. CHANGE: 'signup-otp'
    // (email verification after creating an account) is also standalone.
    if (which === 'forgot' || which === 'reset' || which === 'otp' || which === 'signup-otp') {
        tabs.style.display = 'none';
        signInForm.classList.add('hidden');
        signUpForm.classList.add('hidden');
        forgotForm.classList.toggle('hidden', which !== 'forgot');
        resetForm.classList.toggle('hidden', which !== 'reset');
        otpForm.classList.toggle('hidden', which !== 'otp');
        signupOtpForm.classList.toggle('hidden', which !== 'signup-otp');
        if (which === 'forgot') document.getElementById('forgotEmail').value = '';
        if (which === 'reset') document.getElementById('resetNewPassword').value = '';
        return;
    }

    forgotForm.classList.add('hidden');
    resetForm.classList.add('hidden');
    otpForm.classList.add('hidden');
    signupOtpForm.classList.add('hidden');
    tabs.style.display = currentUser ? 'none' : 'flex';

    if (which === 'signin') {
        tabSignIn.classList.add('active');
        tabSignUp.classList.remove('active');
    } else {
        tabSignUp.classList.add('active');
        tabSignIn.classList.remove('active');
    }

    signInForm.classList.toggle('hidden', which !== 'signin' || !!currentUser);
    signUpForm.classList.toggle('hidden', which !== 'signup' || !!currentUser);
}

function renderAuthState() {
    const tabs = document.querySelector('.auth-tabs');
    const signInForm = document.getElementById('signInForm');
    const signUpForm = document.getElementById('signUpForm');
    const authSuccess = document.getElementById('authSuccess');
    const acctLabel = document.getElementById('acctLabel');

    if (currentUser) {
        tabs.style.display = 'none';
        signInForm.classList.add('hidden');
        signUpForm.classList.add('hidden');
        authSuccess.classList.remove('hidden');
        document.getElementById('welcomeName').textContent = `Welcome, ${currentUser.name.split(' ')[0]}!`;
        acctLabel.textContent = currentUser.name.split(' ')[0];
        loadMyOrders(); // Phase 2: refresh My Orders each time the signed-in view is shown

        // Phase 2: prefill My Profile fields
        document.getElementById('profileName').value = currentUser.name;
        document.getElementById('profilePhone').value = currentUser.phone || '';
        document.getElementById('profileError').textContent = '';
        document.getElementById('changePasswordForm').style.display = 'none';
        document.getElementById('currentPasswordInput').value = '';
        document.getElementById('newPasswordInput').value = '';
        document.getElementById('passwordChangeError').textContent = '';
    } else {
        tabs.style.display = 'flex';
        authSuccess.classList.add('hidden');
        acctLabel.textContent = 'Sign In';
        switchAuthTab('signin');
    }
}

function showAuthError(message) {
    const el = document.getElementById('authError');
    el.textContent = message;
    el.classList.add('show');
}

function clearAuthError() {
    const el = document.getElementById('authError');
    el.textContent = '';
    el.classList.remove('show');
}

// ═══════════════════════════════════════════════════════════════════════
// My Orders / Order Tracking (Phase 2)
// Rendered inside the login modal's signed-in view.
// ═══════════════════════════════════════════════════════════════════════
const ORDER_STATUS_STEPS = ['placed', 'shipped', 'delivered'];

async function loadMyOrders() {
    const container = document.getElementById('myOrdersList');
    if (!container) return;
    try {
        myOrdersCache = await apiFetch('/api/orders');
    } catch (err) {
        container.innerHTML = `<p class="my-orders-empty">Could not load your orders.</p>`;
        return;
    }
    renderMyOrders();
}

function renderMyOrders() {
    const container = document.getElementById('myOrdersList');
    if (!container) return;

    if (myOrdersCache.length === 0) {
        container.innerHTML = `<p class="my-orders-empty">You haven't placed any orders yet.</p>`;
        return;
    }

    container.innerHTML = myOrdersCache.map(order => {
        const itemsSummary = order.items.map(i => `${i.name} × ${i.quantity}`).join(', ');
        const date = new Date(order.createdAt).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
        const isCancelled = order.orderStatus === 'cancelled';
        const currentStepIndex = ORDER_STATUS_STEPS.indexOf(order.orderStatus);

        const tracking = isCancelled
            ? `<div class="my-order-tracking"><div class="tracking-step cancelled"></div><div class="tracking-step cancelled"></div><div class="tracking-step cancelled"></div></div>`
            : `<div class="my-order-tracking">${ORDER_STATUS_STEPS.map((s, i) => `<div class="tracking-step${i <= currentStepIndex ? ' done' : ''}"></div>`).join('')}</div>`;

        // CHANGE: one-time Replace/Return option — only offered once the
        // order is delivered, and only if the customer hasn't used their
        // single replace-or-return allowance on it yet. No "already used"
        // messaging anywhere; the button just isn't there a second time.
        const canRequestReturn = order.orderStatus === 'delivered' && !order.replacementUsed;
        // CHANGE: customer can cancel their own order only while it's still
        // "placed" — once the shop marks it shipped, cancellation has to go
        // through support instead (see cancel_order() in app.py).
        const canCancel = order.orderStatus === 'placed';

        return `
        <div class="my-order-card">
            <div class="my-order-card-top">
                <span class="my-order-number">#${order.orderNumber}</span>
                <span class="status-pill status-${order.orderStatus}">${order.orderStatus}</span>
            </div>
            <div class="my-order-date">${date} · <span class="status-pill status-${order.paymentStatus}">${order.paymentMethod === 'cod' ? 'COD' : order.paymentMethod.toUpperCase()} · ${order.paymentStatus}</span></div>
            <div class="my-order-items">${itemsSummary}</div>
            ${tracking}
            <div class="my-order-total"><span>Total</span><span>₹${order.total.toLocaleString('en-IN')}</span></div>
            ${canRequestReturn ? `<button type="button" class="my-order-return-btn" onclick="openReturnRequestModal('${order.orderNumber}')" style="margin-top:0.6rem; width:100%; padding:0.5rem; border-radius:8px; border:1px solid var(--dark-cream); background:transparent; cursor:pointer; font-family:inherit; font-size:0.85rem;">Request Replacement / Return</button>` : ''}
            ${canCancel ? `<button type="button" class="my-order-cancel-btn" onclick="cancelMyOrder('${order.orderNumber}')" style="margin-top:0.6rem; width:100%; padding:0.5rem; border-radius:8px; border:1px solid #c0392b; color:#c0392b; background:transparent; cursor:pointer; font-family:inherit; font-size:0.85rem;">Cancel Order</button>` : ''}
        </div>`;
    }).join('');
}

// ═══════════════════════════════════════════════════════════════════════
// Return / Replacement Requests (customer side)
// ═══════════════════════════════════════════════════════════════════════
let returnRequestOrderNumber = null;

function openReturnRequestModal(orderNumber) {
    returnRequestOrderNumber = orderNumber;
    document.getElementById('returnRequestOrderNumber').textContent = orderNumber;
    document.getElementById('returnRequestType').value = 'replace';
    document.getElementById('returnRequestReason').value = '';
    document.getElementById('returnRequestError').textContent = '';
    const btn = document.getElementById('returnRequestSubmitBtn');
    if (btn) { btn.disabled = false; btn.textContent = 'Submit Request'; }
    document.getElementById('returnRequestModal').classList.add('active');
}

function closeReturnRequestModal() {
    document.getElementById('returnRequestModal').classList.remove('active');
}

async function submitReturnRequest() {
    if (!returnRequestOrderNumber) return;
    const type = document.getElementById('returnRequestType').value;
    const reason = document.getElementById('returnRequestReason').value.trim();
    const errEl = document.getElementById('returnRequestError');
    const btn = document.getElementById('returnRequestSubmitBtn');
    errEl.textContent = '';
    btn.disabled = true;
    btn.textContent = 'Submitting...';

    try {
        const updatedOrder = await apiFetch(`/api/orders/${returnRequestOrderNumber}/return-request`, {
            method: 'POST',
            body: JSON.stringify({ type, reason }),
        });
        // Keep the local cache in sync so the button disappears immediately
        // without needing a full re-fetch.
        const idx = myOrdersCache.findIndex(o => o.orderNumber === returnRequestOrderNumber);
        if (idx !== -1) myOrdersCache[idx] = { ...myOrdersCache[idx], ...updatedOrder };
        renderMyOrders();
        closeReturnRequestModal();
        showNotification('Your request has been submitted. Our team will reach out soon.');
    } catch (err) {
        errEl.textContent = err.message || 'Could not submit your request. Please try again.';
        btn.disabled = false;
        btn.textContent = 'Submit Request';
    }
}

// CHANGE: customer-initiated cancellation, for orders still in "placed"
// status. Simple confirm() + API call — no modal needed since there's
// nothing to fill in (unlike the return/replace request above).
async function cancelMyOrder(orderNumber) {
    if (!confirm(`Cancel order #${orderNumber}? This can't be undone.`)) return;

    try {
        const updatedOrder = await apiFetch(`/api/orders/${orderNumber}/cancel`, { method: 'POST' });
        const idx = myOrdersCache.findIndex(o => o.orderNumber === orderNumber);
        if (idx !== -1) myOrdersCache[idx] = { ...myOrdersCache[idx], ...updatedOrder };
        renderMyOrders();
        showNotification('Your order has been cancelled.');
    } catch (err) {
        alert(err.message || 'Could not cancel this order. Please try again.');
    }
}

// ═══════════════════════════════════════════════════════════════════════
// My Profile (Phase 2)
// Rendered inside the login modal's signed-in view, below My Orders.
// ═══════════════════════════════════════════════════════════════════════
async function saveProfile() {
    const errEl = document.getElementById('profileError');
    errEl.textContent = '';

    const name = document.getElementById('profileName').value.trim();
    const phone = document.getElementById('profilePhone').value.trim();
    if (!name || !phone) {
        errEl.textContent = 'Name and phone are required.';
        return;
    }

    try {
        const data = await apiFetch('/api/auth/profile', {
            method: 'PUT',
            body: JSON.stringify({ name, phone }),
        });
        currentUser = data.user;
        document.getElementById('welcomeName').textContent = `Welcome, ${currentUser.name.split(' ')[0]}!`;
        document.getElementById('acctLabel').textContent = currentUser.name.split(' ')[0];
        showNotification('Profile updated.');
    } catch (err) {
        errEl.textContent = err.message;
    }
}

function toggleChangePasswordForm() {
    const form = document.getElementById('changePasswordForm');
    form.style.display = form.style.display === 'none' ? 'block' : 'none';
}

async function changePassword() {
    const errEl = document.getElementById('passwordChangeError');
    errEl.textContent = '';

    const currentPassword = document.getElementById('currentPasswordInput').value;
    const newPassword = document.getElementById('newPasswordInput').value;
    if (!currentPassword || !newPassword) {
        errEl.textContent = 'Please fill in both password fields.';
        return;
    }

    try {
        await apiFetch('/api/auth/change-password', {
            method: 'POST',
            body: JSON.stringify({ currentPassword, newPassword }),
        });
        document.getElementById('currentPasswordInput').value = '';
        document.getElementById('newPasswordInput').value = '';
        document.getElementById('changePasswordForm').style.display = 'none';
        showNotification('Password updated.');
    } catch (err) {
        errEl.textContent = err.message;
    }
}

// ═══════════════════════════════════════════════════════════════════════
// Wishlist (Phase 2)
// Heart-shaped toggle buttons on product cards, the product detail page,
// and the quick-view modal. Requires a signed-in account (so it persists
// across visits) — clicking it while signed out opens the existing login
// modal instead of silently failing.
// ═══════════════════════════════════════════════════════════════════════

function setupWishlistUI() {
    const wishlistBtn = document.getElementById('wishlistBtn');
    const wishlistModal = document.getElementById('wishlistModal');
    const closeWishlistBtn = document.getElementById('closeWishlist');

    wishlistBtn.addEventListener('click', () => {
        if (!currentUser) {
            document.getElementById('loginModal').classList.add('active');
            renderAuthState();
            showAuthError('Please sign in to view and save your wishlist.');
            return;
        }
        renderWishlistModal();
        wishlistModal.classList.add('active');
    });

    closeWishlistBtn.addEventListener('click', () => wishlistModal.classList.remove('active'));

    wishlistModal.addEventListener('click', function (e) {
        if (e.target === this) wishlistModal.classList.remove('active');
    });
}

async function loadServerWishlist() {
    if (!currentUser) {
        wishlist = [];
        wishlistIds = new Set();
        updateWishlistCount();
        return;
    }
    try {
        wishlist = await apiFetch('/api/wishlist');
        wishlistIds = new Set(wishlist.map(p => p.id));
    } catch (err) {
        console.error('Failed to load wishlist:', err);
    }
    updateWishlistCount();
}

function updateWishlistCount() {
    const el = document.getElementById('wishlistCount');
    if (el) el.textContent = wishlist.length;
}

async function toggleWishlist(productId) {
    if (!currentUser) {
        document.getElementById('loginModal').classList.add('active');
        renderAuthState();
        showAuthError('Please sign in to save items to your wishlist.');
        return;
    }

    const alreadyIn = wishlistIds.has(productId);
    try {
        if (alreadyIn) {
            wishlist = await apiFetch(`/api/wishlist/${productId}`, { method: 'DELETE' });
        } else {
            wishlist = await apiFetch(`/api/wishlist/${productId}`, { method: 'POST' });
        }
        wishlistIds = new Set(wishlist.map(p => p.id));
    } catch (err) {
        showNotification('Could not update wishlist: ' + err.message);
        return;
    }

    updateWishlistCount();
    showNotification(alreadyIn ? 'Removed from wishlist.' : 'Added to wishlist!');

    // Refresh whichever views show heart state
    renderProducts();
    const detailBtn = document.getElementById('detailWishlistBtn');
    if (currentPage === 'product' && detailBtn) updateWishlistButton(detailBtn, productId);
    const modalBtn = document.getElementById('modalWishlistBtn');
    if (document.getElementById('productModal').classList.contains('active') && modalBtn) {
        updateWishlistButton(modalBtn, productId);
    }
    if (document.getElementById('wishlistModal').classList.contains('active')) renderWishlistModal();
}

function updateWishlistButton(btn, productId) {
    const isWishlisted = wishlistIds.has(productId);
    btn.innerHTML = isWishlisted ? '♥ In Wishlist' : '♡ Wishlist';
    btn.style.background = isWishlisted ? 'var(--maroon)' : 'none';
    btn.style.color = isWishlisted ? 'white' : 'var(--maroon)';
}

function renderWishlistModal() {
    const container = document.getElementById('wishlistItems');

    if (wishlist.length === 0) {
        container.innerHTML = `
            <div class="wishlist-empty">
                <div class="empty-cart-icon">♡</div>
                <p>Your wishlist is empty</p>
                <p>Tap the heart on any saree to save it here.</p>
            </div>
        `;
        return;
    }

    container.innerHTML = wishlist.map(p => {
        const imgContent = p.imageData
            ? `<img src="${p.imageData}" alt="${p.name}">`
            : p.emoji;
        return `
        <div class="wishlist-modal-item">
            <div class="wishlist-modal-image" onclick="document.getElementById('wishlistModal').classList.remove('active'); openProduct(${p.id});">${imgContent}</div>
            <div class="wishlist-modal-details">
                <h3 class="wishlist-modal-name" onclick="document.getElementById('wishlistModal').classList.remove('active'); openProduct(${p.id});">${p.name}</h3>
                <p class="wishlist-modal-price">₹${p.price.toLocaleString('en-IN')}</p>
                <div class="wishlist-modal-actions">
                    <button class="wishlist-add-cart-btn" onclick="addToCart(${p.id})">Add to Cart</button>
                    <button class="wishlist-remove-btn" onclick="toggleWishlist(${p.id})">Remove</button>
                </div>
            </div>
        </div>`;
    }).join('');
}

// ═══════════════════════════════════════════════════════════════════════
// Reviews (Phase 2)
// Product detail page: average rating summary, a star-rating write form
// (signed-in customers only), and the list of existing reviews.
// ═══════════════════════════════════════════════════════════════════════
function setupReviewStars() {
    const picker = document.getElementById('reviewStarPicker');
    if (!picker) return;
    const stars = Array.from(picker.querySelectorAll('span'));

    function paint(rating) {
        stars.forEach(s => s.classList.toggle('selected', parseInt(s.dataset.star, 10) <= rating));
    }

    stars.forEach(s => {
        s.addEventListener('mouseenter', () => paint(parseInt(s.dataset.star, 10)));
        s.addEventListener('click', () => {
            selectedReviewRating = parseInt(s.dataset.star, 10);
            paint(selectedReviewRating);
        });
    });
    picker.addEventListener('mouseleave', () => paint(selectedReviewRating));
}

async function loadReviews(productId) {
    currentReviewProductId = productId;
    selectedReviewRating = 0;

    const commentEl = document.getElementById('reviewComment');
    if (commentEl) commentEl.value = '';
    document.getElementById('reviewError').textContent = '';
    document.querySelectorAll('#reviewStarPicker span').forEach(s => s.classList.remove('selected'));

    document.getElementById('reviewSignInPrompt').style.display = currentUser ? 'none' : 'block';
    document.getElementById('reviewFormFields').style.display = currentUser ? 'block' : 'none';

    let data;
    try {
        data = await apiFetch(`/api/products/${productId}/reviews`);
    } catch (err) {
        document.getElementById('reviewsSummary').innerHTML = '';
        document.getElementById('reviewsList').innerHTML = `<p class="reviews-empty">Could not load reviews.</p>`;
        return;
    }
    renderReviews(data);
}

function renderReviews(data) {
    const { reviews, summary } = data;

    const summaryEl = document.getElementById('reviewsSummary');
    if (summary.count === 0) {
        summaryEl.innerHTML = `<span class="reviews-count">No reviews yet — be the first to share your experience.</span>`;
    } else {
        summaryEl.innerHTML = `
            <span class="reviews-avg">${summary.average}</span>
            <span class="reviews-stars">${'★'.repeat(Math.round(summary.average))}${'☆'.repeat(5 - Math.round(summary.average))}</span>
            <span class="reviews-count">based on ${summary.count} review${summary.count === 1 ? '' : 's'}</span>
        `;
    }

    const listEl = document.getElementById('reviewsList');
    if (reviews.length === 0) {
        listEl.innerHTML = '';
        return;
    }
    listEl.innerHTML = reviews.map(r => {
        const date = new Date(r.createdAt).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
        return `
        <div class="review-card">
            <div class="review-card-top">
                <span class="review-name">${escapeHtml(r.customerName)}</span>
                <span class="review-date">${date}</span>
            </div>
            <div class="review-stars">${'★'.repeat(r.rating)}${'☆'.repeat(5 - r.rating)}</div>
            ${r.comment ? `<p class="review-comment">${escapeHtml(r.comment)}</p>` : ''}
        </div>`;
    }).join('');
}

async function submitReview() {
    const errEl = document.getElementById('reviewError');
    errEl.textContent = '';

    if (!currentUser) {
        document.getElementById('loginModal').classList.add('active');
        renderAuthState();
        return;
    }
    if (!selectedReviewRating) {
        errEl.textContent = 'Please select a star rating.';
        return;
    }

    const comment = document.getElementById('reviewComment').value.trim();

    try {
        const data = await apiFetch(`/api/products/${currentReviewProductId}/reviews`, {
            method: 'POST',
            body: JSON.stringify({ rating: selectedReviewRating, comment }),
        });
        renderReviews(data);
        document.getElementById('reviewComment').value = '';
        selectedReviewRating = 0;
        document.querySelectorAll('#reviewStarPicker span').forEach(s => s.classList.remove('selected'));
        showNotification('Thank you for your review!');
    } catch (err) {
        errEl.textContent = err.message;
    }
}

document.addEventListener('DOMContentLoaded', setupReviewStars);

// ═══════════════════════════════════════════════════════════════════════
// Admin Product Management — now backed by /api/admin/* (Flask + SQLite)
// instead of an in-memory array. Requires a real authenticated session
// (see admin.html), enforced server-side by @admin_required on the API.
// ═══════════════════════════════════════════════════════════════════════

// ─── Admin Stats ────────────────────────────────────────────────────────
async function updateAdminStats() {
    try {
        const stats = await apiFetch('/api/admin/stats');
        document.getElementById('statTotal').textContent = stats.total;
        document.getElementById('statBridal').textContent = stats.byCategory.bridal || 0;
        document.getElementById('statFestive').textContent = stats.byCategory.festive || 0;
        document.getElementById('statAvgPrice').textContent = '₹' + stats.avgPrice.toLocaleString('en-IN');
    } catch (err) {
        console.error('Failed to load admin stats:', err);
    }
}

// ─── Admin Product Table ────────────────────────────────────────────────
async function refreshAdminTable() {
    const tbody = document.getElementById('adminTableBody');
    if (!tbody) return;

    let adminProducts;
    try {
        adminProducts = await apiFetch('/api/admin/products');
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="8">Failed to load products: ${err.message}</td></tr>`;
        return;
    }

    tbody.innerHTML = adminProducts.map(p => {
        const imgCell = p.imageData
            ? `<img src="${p.imageData}" alt="${escapeHtml(p.name)}" style="width:48px;height:48px;object-fit:cover;border-radius:6px;">`
            : `<span style="font-size:2rem;">${escapeHtml(p.emoji)}</span>`;
        // CHANGE (Phase 5): compact flag pills, so admins can see storefront
        // placement (Featured / Bestseller / New Arrival) at a glance.
        const flagPills = [
            p.featured ? '<span class="admin-badge-pill" style="background:#e8dfc8;">Featured</span>' : '',
            p.bestseller ? '<span class="admin-badge-pill" style="background:#f0d9c0;">Bestseller</span>' : '',
            p.newArrival ? '<span class="admin-badge-pill" style="background:#d9e8dc;">New</span>' : '',
        ].filter(Boolean).join(' ') || '—';
        return `
        <tr>
            <td>${p.id}</td>
            <td>${imgCell}</td>
            <td>${escapeHtml(p.name)}</td>
            <td style="text-transform:capitalize;">${escapeHtml(p.category)}</td>
            <td>₹${p.price.toLocaleString('en-IN')}</td>
            <td>${p.badge ? `<span class="admin-badge-pill">${escapeHtml(p.badge)}</span>` : '—'}</td>
            <td>${flagPills}</td>
            <td>
                <button class="admin-btn admin-btn-edit" onclick="openEditProductModal(${p.id})">Edit</button>
                <button class="admin-btn admin-btn-delete" onclick="deleteProduct(${p.id})">Delete</button>
            </td>
        </tr>`;
    }).join('');

    // Keep the in-memory cache used by the edit modal in sync
    window.__adminProductsCache = adminProducts;

    updateAdminStats();
}

// ─────────────────────────────────────────────────────────────────────────
// Admin Tabs (Phase 2): Products / Orders / Coupons
// ─────────────────────────────────────────────────────────────────────────
function switchAdminTab(which) {
    document.querySelectorAll('.admin-tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.admin-tab-panel').forEach(p => p.classList.remove('active'));

    if (which === 'products') {
        document.getElementById('adminTabProducts').classList.add('active');
        document.getElementById('adminDashboard').classList.add('active');
    } else if (which === 'orders') {
        document.getElementById('adminTabOrders').classList.add('active');
        document.getElementById('adminOrdersPanel').classList.add('active');
        refreshAdminOrders();
    } else if (which === 'returns') {
        document.getElementById('adminTabReturns').classList.add('active');
        document.getElementById('adminReturnsPanel').classList.add('active');
        refreshAdminReturns();
    } else if (which === 'coupons') {
        document.getElementById('adminTabCoupons').classList.add('active');
        document.getElementById('adminCouponsPanel').classList.add('active');
        refreshAdminCoupons();
    } else if (which === 'banners') {
        document.getElementById('adminTabBanners').classList.add('active');
        document.getElementById('adminBannersPanel').classList.add('active');
        refreshAdminBanners();
    } else if (which === 'analytics') {
        document.getElementById('adminTabAnalytics').classList.add('active');
        document.getElementById('adminAnalyticsPanel').classList.add('active');
        refreshAdminAnalytics();
    } else if (which === 'customers') {
        document.getElementById('adminTabCustomers').classList.add('active');
        document.getElementById('adminCustomersPanel').classList.add('active');
        refreshAdminCustomers();
    } else if (which === 'messages') {
        document.getElementById('adminTabMessages').classList.add('active');
        document.getElementById('adminMessagesPanel').classList.add('active');
        refreshAdminMessages();
    } else if (which === 'newsletter') {
        document.getElementById('adminTabNewsletter').classList.add('active');
        document.getElementById('adminNewsletterPanel').classList.add('active');
        refreshAdminNewsletter();
    }
}

// ─── Admin Orders ───────────────────────────────────────────────────────
async function refreshAdminOrders() {
    const tbody = document.getElementById('adminOrdersBody');
    if (!tbody) return;

    let orders;
    try {
        orders = await apiFetch('/api/admin/orders');
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="7">Failed to load orders: ${err.message}</td></tr>`;
        return;
    }

    if (orders.length === 0) {
        tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; color:var(--stone-soft);">No orders yet.</td></tr>`;
        return;
    }

    const statuses = ['placed', 'shipped', 'delivered', 'cancelled'];
    tbody.innerHTML = orders.map(o => {
        const itemsSummary = o.items.map(i => `${escapeHtml(i.name)} × ${i.quantity}`).join(', ');
        const date = new Date(o.createdAt).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });

        return `
        <tr>
            <td>${escapeHtml(o.orderNumber)}</td>
            <td>${escapeHtml(o.customerName)}<br><span style="font-size:0.82rem; color:var(--stone-soft);">${escapeHtml(o.customerPhone)}</span></td>
            <td style="max-width:220px;">${itemsSummary}</td>
            <td>₹${o.total.toLocaleString('en-IN')}</td>
            <td><span class="status-pill status-${o.paymentStatus}">${o.paymentMethod === 'cod' ? 'COD' : o.paymentMethod.toUpperCase()} · ${o.paymentStatus}</span></td>
            <td>
                <select onchange="updateOrderStatus(${o.id}, this.value)" style="padding:0.3rem 0.5rem; border-radius:6px; border:1px solid var(--dark-cream); font-family:inherit;">
                    ${statuses.map(s => `<option value="${s}" ${s === o.orderStatus ? 'selected' : ''}>${s.charAt(0).toUpperCase() + s.slice(1)}</option>`).join('')}
                </select>
            </td>
            <td>${date}</td>
        </tr>`;
    }).join('');
}

async function updateOrderStatus(orderId, status) {
    try {
        await apiFetch(`/api/admin/orders/${orderId}`, { method: 'PUT', body: JSON.stringify({ orderStatus: status }) });
        showNotification('Order status updated.');
    } catch (err) {
        alert('Failed to update order: ' + err.message);
    }
    refreshAdminOrders();
}

// ─── Admin Returns / Replacements ──────────────────────────────────────
async function refreshAdminReturns() {
    const tbody = document.getElementById('adminReturnsBody');
    if (!tbody) return;

    let requests;
    try {
        requests = await apiFetch('/api/admin/return-requests');
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="6">Failed to load requests: ${err.message}</td></tr>`;
        return;
    }

    if (requests.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; color:var(--stone-soft);">No return/replacement requests yet.</td></tr>`;
        return;
    }

    const statuses = ['pending', 'approved', 'rejected', 'completed'];
    tbody.innerHTML = requests.map(r => {
        const date = new Date(r.createdAt).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
        return `
        <tr>
            <td>${escapeHtml(r.orderNumber)}</td>
            <td>${escapeHtml(r.customerName)}<br><span style="font-size:0.82rem; color:var(--stone-soft);">${escapeHtml(r.customerPhone)}</span></td>
            <td style="text-transform:capitalize;">${escapeHtml(r.type)}</td>
            <td style="max-width:220px;">${escapeHtml(r.reason) || '—'}</td>
            <td>${date}</td>
            <td>
                <select onchange="updateReturnRequestStatus(${r.id}, this.value)" style="padding:0.3rem 0.5rem; border-radius:6px; border:1px solid var(--dark-cream); font-family:inherit;">
                    ${statuses.map(s => `<option value="${s}" ${s === r.status ? 'selected' : ''}>${s.charAt(0).toUpperCase() + s.slice(1)}</option>`).join('')}
                </select>
            </td>
        </tr>`;
    }).join('');
}

async function updateReturnRequestStatus(requestId, status) {
    try {
        await apiFetch(`/api/admin/return-requests/${requestId}`, { method: 'PUT', body: JSON.stringify({ status }) });
        showNotification('Request status updated.');
    } catch (err) {
        alert('Failed to update request: ' + err.message);
    }
    refreshAdminReturns();
}

// ─── Admin Coupons ──────────────────────────────────────────────────────
async function refreshAdminCoupons() {
    const tbody = document.getElementById('adminCouponsBody');
    if (!tbody) return;

    let coupons;
    try {
        coupons = await apiFetch('/api/admin/coupons');
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="7">Failed to load coupons: ${err.message}</td></tr>`;
        return;
    }

    if (coupons.length === 0) {
        tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; color:var(--stone-soft);">No coupons yet.</td></tr>`;
        return;
    }

    tbody.innerHTML = coupons.map(c => {
        const discountLabel = c.discountType === 'percent' ? `${c.discountValue}%` : `₹${c.discountValue.toLocaleString('en-IN')}`;
        const usesLabel = c.maxUses ? `${c.usedCount} / ${c.maxUses}` : `${c.usedCount} / ∞`;
        const expiresLabel = c.expiresAt ? new Date(c.expiresAt).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' }) : '—';
        return `
        <tr>
            <td><strong>${escapeHtml(c.code)}</strong></td>
            <td>${discountLabel}</td>
            <td>${c.minOrder ? '₹' + c.minOrder.toLocaleString('en-IN') : '—'}</td>
            <td>${usesLabel}</td>
            <td>${expiresLabel}</td>
            <td><span class="status-pill ${c.isActive ? 'status-delivered' : 'status-cancelled'}">${c.isActive ? 'Active' : 'Disabled'}</span></td>
            <td>
                <button class="admin-btn admin-btn-edit" onclick="toggleCouponActive(${c.id}, ${!c.isActive})">${c.isActive ? 'Disable' : 'Enable'}</button>
                <button class="admin-btn admin-btn-delete" onclick="deleteCoupon(${c.id}, '${escapeHtml(c.code)}')">Delete</button>
            </td>
        </tr>`;
    }).join('');
}

async function createCoupon() {
    const errEl = document.getElementById('couponFormError');
    errEl.textContent = '';

    const code = document.getElementById('couponFormCode').value.trim();
    const discountType = document.getElementById('couponFormType').value;
    const discountValue = document.getElementById('couponFormValue').value;
    const minOrder = document.getElementById('couponFormMinOrder').value;
    const maxUses = document.getElementById('couponFormMaxUses').value;
    const expiresAt = document.getElementById('couponFormExpires').value;

    if (!code || !discountValue) {
        errEl.textContent = 'Please enter a code and discount value.';
        return;
    }

    try {
        await apiFetch('/api/admin/coupons', {
            method: 'POST',
            body: JSON.stringify({
                code, discountType,
                discountValue: parseInt(discountValue, 10),
                minOrder: minOrder ? parseInt(minOrder, 10) : 0,
                maxUses: maxUses ? parseInt(maxUses, 10) : null,
                expiresAt: expiresAt || null,
            }),
        });
    } catch (err) {
        errEl.textContent = err.message;
        return;
    }

    document.getElementById('couponFormCode').value = '';
    document.getElementById('couponFormValue').value = '';
    document.getElementById('couponFormMinOrder').value = '';
    document.getElementById('couponFormMaxUses').value = '';
    document.getElementById('couponFormExpires').value = '';
    showNotification('Coupon created!');
    refreshAdminCoupons();
}

async function toggleCouponActive(couponId, makeActive) {
    try {
        await apiFetch(`/api/admin/coupons/${couponId}`, { method: 'PUT', body: JSON.stringify({ isActive: makeActive }) });
    } catch (err) {
        alert('Failed to update coupon: ' + err.message);
    }
    refreshAdminCoupons();
}

async function deleteCoupon(couponId, code) {
    if (!confirm(`Delete coupon "${code}"? This cannot be undone.`)) return;
    try {
        await apiFetch(`/api/admin/coupons/${couponId}`, { method: 'DELETE' });
        showNotification('Coupon deleted.');
    } catch (err) {
        alert('Failed to delete coupon: ' + err.message);
    }
    refreshAdminCoupons();
}

// ═══════════════════════════════════════════════════════════════════════
// Phase 5: Admin — Banners (homepage promo strip)
// ═══════════════════════════════════════════════════════════════════════
async function refreshAdminBanners() {
    const tbody = document.getElementById('adminBannersBody');
    if (!tbody) return;

    let banners;
    try {
        banners = await apiFetch('/api/admin/banners');
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="5">Failed to load banners: ${err.message}</td></tr>`;
        return;
    }

    if (banners.length === 0) {
        tbody.innerHTML = `<tr><td colspan="5" style="text-align:center; color:var(--stone-soft);">No banners yet — add one above.</td></tr>`;
        return;
    }

    tbody.innerHTML = banners.map(b => `
        <tr>
            <td>${b.displayOrder}</td>
            <td>
                <strong>${escapeHtml(b.emoji)} ${escapeHtml(b.title)}</strong>
                ${b.subtitle ? `<br><span style="color:var(--stone-soft); font-size:0.85rem;">${escapeHtml(b.subtitle)}</span>` : ''}
            </td>
            <td>${b.linkUrl ? escapeHtml(b.linkLabel || b.linkUrl) : '—'}</td>
            <td><span class="status-pill ${b.isActive ? 'status-delivered' : 'status-cancelled'}">${b.isActive ? 'Active' : 'Disabled'}</span></td>
            <td>
                <button class="admin-btn admin-btn-edit" onclick="toggleBannerActive(${b.id}, ${!b.isActive})">${b.isActive ? 'Disable' : 'Enable'}</button>
                <button class="admin-btn admin-btn-delete" onclick="deleteBanner(${b.id}, '${escapeHtml(b.title)}')">Delete</button>
            </td>
        </tr>`).join('');
}

async function createBanner() {
    const errEl = document.getElementById('bannerFormError');
    errEl.textContent = '';

    const title = document.getElementById('bannerFormTitle').value.trim();
    const subtitle = document.getElementById('bannerFormSubtitle').value.trim();
    const emoji = document.getElementById('bannerFormEmoji').value.trim() || '🎉';
    const linkUrl = document.getElementById('bannerFormLinkUrl').value.trim();
    const linkLabel = document.getElementById('bannerFormLinkLabel').value.trim();
    const displayOrder = parseInt(document.getElementById('bannerFormOrder').value || '0', 10);

    if (!title) {
        errEl.textContent = 'Please enter a banner title.';
        return;
    }

    try {
        await apiFetch('/api/admin/banners', {
            method: 'POST',
            body: JSON.stringify({ title, subtitle, emoji, linkUrl, linkLabel, displayOrder, isActive: true }),
        });
    } catch (err) {
        errEl.textContent = err.message;
        return;
    }

    document.getElementById('bannerFormTitle').value = '';
    document.getElementById('bannerFormSubtitle').value = '';
    document.getElementById('bannerFormEmoji').value = '';
    document.getElementById('bannerFormLinkUrl').value = '';
    document.getElementById('bannerFormLinkLabel').value = '';
    document.getElementById('bannerFormOrder').value = '0';
    showNotification('Banner created!');
    refreshAdminBanners();
    loadBanners(); // refresh the live homepage strip too
}

async function toggleBannerActive(bannerId, makeActive) {
    try {
        await apiFetch(`/api/admin/banners/${bannerId}`, { method: 'PUT', body: JSON.stringify({ isActive: makeActive }) });
    } catch (err) {
        alert('Failed to update banner: ' + err.message);
    }
    refreshAdminBanners();
    loadBanners();
}

async function deleteBanner(bannerId, title) {
    if (!confirm(`Delete banner "${title}"? This cannot be undone.`)) return;
    try {
        await apiFetch(`/api/admin/banners/${bannerId}`, { method: 'DELETE' });
        showNotification('Banner deleted.');
    } catch (err) {
        alert('Failed to delete banner: ' + err.message);
    }
    refreshAdminBanners();
    loadBanners();
}

// ═══════════════════════════════════════════════════════════════════════
// Phase 5: Admin — Analytics dashboard
// Lightweight, dependency-free charts: plain divs sized with CSS instead
// of pulling in a charting library, consistent with this project's
// "no extra infra unless it earns its keep" approach.
// ═══════════════════════════════════════════════════════════════════════
async function refreshAdminAnalytics() {
    const days = document.getElementById('analyticsDaysFilter')?.value || 30;
    let data;
    try {
        data = await apiFetch(`/api/admin/analytics?days=${days}`);
    } catch (err) {
        document.getElementById('chartRevenue').innerHTML = `<p style="color:var(--stone-soft);">Failed to load analytics: ${escapeHtml(err.message)}</p>`;
        return;
    }

    document.getElementById('statRevenue').textContent = `₹${data.totals.revenue.toLocaleString('en-IN')}`;
    document.getElementById('statOrders').textContent = data.totals.orders.toLocaleString('en-IN');
    document.getElementById('statCustomers').textContent = data.totals.customers.toLocaleString('en-IN');
    document.getElementById('statAOV').textContent = `₹${data.totals.avgOrderValue.toLocaleString('en-IN')}`;
    document.getElementById('statViews').textContent = data.totals.viewsInWindow.toLocaleString('en-IN');

    renderBarChart('chartRevenue', data.revenueSeries, v => `₹${v.toLocaleString('en-IN')}`);
    renderBarChart('chartOrders', data.ordersSeries, v => `${v}`);
    renderBarChart('chartViews', data.viewsSeries, v => `${v}`);

    const statusOrder = ['placed', 'shipped', 'delivered', 'cancelled'];
    const statusCounts = data.orderStatusCounts || {};
    const maxStatus = Math.max(1, ...statusOrder.map(s => statusCounts[s] || 0));
    document.getElementById('chartOrderStatus').innerHTML = statusOrder.map(s => `
        <div class="analytics-bar-row">
            <span class="analytics-bar-label" style="text-transform:capitalize;">${s}</span>
            <div class="analytics-bar-track"><div class="analytics-bar-fill" style="width:${((statusCounts[s] || 0) / maxStatus) * 100}%;"></div></div>
            <span class="analytics-bar-value">${statusCounts[s] || 0}</span>
        </div>`).join('');

    document.getElementById('listTopRevenue').innerHTML = data.topProductsByRevenue.length
        ? data.topProductsByRevenue.map(p => `
            <div class="analytics-list-row">
                <span>${escapeHtml(p.name)}</span>
                <span>₹${p.revenue.toLocaleString('en-IN')} · ${p.units} sold</span>
            </div>`).join('')
        : '<p style="color:var(--stone-soft);">No sales yet in this window.</p>';

    document.getElementById('listTopViews').innerHTML = data.topProductsByViews.length
        ? data.topProductsByViews.map(p => `
            <div class="analytics-list-row">
                <span>${escapeHtml(p.name)}</span>
                <span>${p.views.toLocaleString('en-IN')} views</span>
            </div>`).join('')
        : '<p style="color:var(--stone-soft);">No product views yet in this window.</p>';
}

function renderBarChart(containerId, series, formatValue) {
    const container = document.getElementById(containerId);
    if (!container) return;
    const max = Math.max(1, ...series.map(p => p.value));
    container.innerHTML = `
        <div class="analytics-bars-row">
            ${series.map(p => `
                <div class="analytics-col" title="${p.date}: ${formatValue(p.value)}">
                    <div class="analytics-col-bar" style="height:${Math.max(2, (p.value / max) * 100)}%;"></div>
                </div>`).join('')}
        </div>
        <div class="analytics-axis">
            <span>${series[0]?.date.slice(5) || ''}</span>
            <span>${series[series.length - 1]?.date.slice(5) || ''}</span>
        </div>`;
}

// ─────────────────────────────────────────────────────────────────────────
// CHANGE (Phase 6): Admin — Customers tab
// ─────────────────────────────────────────────────────────────────────────
let customerSearchDebounceTimer = null;
let currentCustomerDetailId = null;

function debouncedRefreshAdminCustomers() {
    clearTimeout(customerSearchDebounceTimer);
    customerSearchDebounceTimer = setTimeout(refreshAdminCustomers, 350);
}

async function refreshAdminCustomers() {
    const tbody = document.getElementById('adminCustomersBody');
    if (!tbody) return;
    const search = document.getElementById('customerSearchInput')?.value.trim() || '';

    let customers;
    try {
        const qs = search ? `?search=${encodeURIComponent(search)}` : '';
        customers = await apiFetch(`/api/admin/customers${qs}`);
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="8">Failed to load customers: ${escapeHtml(err.message)}</td></tr>`;
        return;
    }

    if (customers.length === 0) {
        tbody.innerHTML = `<tr><td colspan="8" style="text-align:center; color:var(--stone-soft);">No customers found.</td></tr>`;
        return;
    }

    // Cache for the detail modal so it doesn't need a second round trip
    // just to populate the header before the /api/admin/customers/<id>
    // call (which additionally carries the full order history) returns.
    window.__adminCustomersCache = customers;

    tbody.innerHTML = customers.map(c => {
        const joined = new Date(c.createdAt).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
        return `
        <tr>
            <td>${escapeHtml(c.name)}</td>
            <td>${escapeHtml(c.email)}</td>
            <td>${escapeHtml(c.phone || '—')}</td>
            <td>${c.orderCount}</td>
            <td>₹${(c.totalSpent || 0).toLocaleString('en-IN')}</td>
            <td><span class="status-pill ${c.isBlocked ? 'status-cancelled' : 'status-delivered'}">${c.isBlocked ? 'Blocked' : 'Active'}</span></td>
            <td>${joined}</td>
            <td><button class="admin-btn admin-btn-edit" onclick="openCustomerDetailModal(${c.id})">View</button></td>
        </tr>`;
    }).join('');
}

async function openCustomerDetailModal(customerId) {
    currentCustomerDetailId = customerId;
    document.getElementById('customerDetailName').textContent = 'Loading…';
    document.getElementById('customerDetailMeta').textContent = '';
    document.getElementById('customerAdminNotes').value = '';
    document.getElementById('customerOrderHistory').innerHTML = '';
    document.getElementById('customerDetailModal').classList.add('active');

    let customer;
    try {
        customer = await apiFetch(`/api/admin/customers/${customerId}`);
    } catch (err) {
        document.getElementById('customerDetailName').textContent = 'Error';
        document.getElementById('customerDetailMeta').textContent = err.message;
        return;
    }

    document.getElementById('customerDetailName').textContent = customer.name;
    document.getElementById('customerDetailMeta').textContent =
        `${customer.email} · ${customer.phone || 'no phone on file'} · joined ${new Date(customer.createdAt).toLocaleDateString('en-IN')}`;
    document.getElementById('customerAdminNotes').value = customer.adminNotes || '';

    const blockBtn = document.getElementById('customerBlockToggleBtn');
    blockBtn.textContent = customer.isBlocked ? 'Unblock Account' : 'Block Account';
    blockBtn.dataset.blocked = customer.isBlocked ? '1' : '0';

    const orders = customer.orders || [];
    document.getElementById('customerOrderHistory').innerHTML = orders.length
        ? `<div class="admin-table-wrap"><table class="admin-table"><thead><tr>
             <th>Order #</th><th>Total (₹)</th><th>Status</th><th>Placed</th>
           </tr></thead><tbody>${orders.map(o => `
             <tr>
                <td>${escapeHtml(o.orderNumber)}</td>
                <td>₹${o.total.toLocaleString('en-IN')}</td>
                <td><span class="status-pill status-${o.orderStatus}">${o.orderStatus}</span></td>
                <td>${new Date(o.createdAt).toLocaleDateString('en-IN')}</td>
             </tr>`).join('')}</tbody></table></div>`
        : '<p style="color:var(--stone-soft);">No orders placed yet.</p>';
}

function closeCustomerDetailModal() {
    document.getElementById('customerDetailModal').classList.remove('active');
    currentCustomerDetailId = null;
}

async function toggleCustomerBlockFromModal() {
    if (!currentCustomerDetailId) return;
    const blockBtn = document.getElementById('customerBlockToggleBtn');
    const nextBlocked = blockBtn.dataset.blocked !== '1';

    if (nextBlocked && !confirm('Block this customer? They will be signed out and unable to sign in until unblocked.')) return;

    try {
        await apiFetch(`/api/admin/customers/${currentCustomerDetailId}`, {
            method: 'PUT',
            body: JSON.stringify({ isBlocked: nextBlocked }),
        });
        showNotification(nextBlocked ? 'Customer blocked.' : 'Customer unblocked.');
        await openCustomerDetailModal(currentCustomerDetailId);
        await refreshAdminCustomers();
    } catch (err) {
        alert('Failed to update customer: ' + err.message);
    }
}

async function saveCustomerNotesFromModal() {
    if (!currentCustomerDetailId) return;
    const notes = document.getElementById('customerAdminNotes').value;
    try {
        await apiFetch(`/api/admin/customers/${currentCustomerDetailId}`, {
            method: 'PUT',
            body: JSON.stringify({ adminNotes: notes }),
        });
        showNotification('Notes saved.');
    } catch (err) {
        alert('Failed to save notes: ' + err.message);
    }
}

async function deleteCustomerFromModal() {
    if (!currentCustomerDetailId) return;
    if (!confirm('Delete this customer permanently? This only works if they have no order history.')) return;
    try {
        await apiFetch(`/api/admin/customers/${currentCustomerDetailId}`, { method: 'DELETE' });
        showNotification('Customer deleted.');
        closeCustomerDetailModal();
        await refreshAdminCustomers();
    } catch (err) {
        alert(err.message || 'Failed to delete customer.');
    }
}

// ─────────────────────────────────────────────────────────────────────────
// CHANGE (Phase 6): Admin — Contact Messages tab
// ─────────────────────────────────────────────────────────────────────────
async function refreshAdminMessages() {
    const tbody = document.getElementById('adminMessagesBody');
    if (!tbody) return;
    const status = document.getElementById('messageStatusFilter')?.value || '';

    let messages;
    try {
        const qs = status ? `?status=${encodeURIComponent(status)}` : '';
        messages = await apiFetch(`/api/admin/contact-messages${qs}`);
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="6">Failed to load messages: ${escapeHtml(err.message)}</td></tr>`;
        return;
    }

    if (messages.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; color:var(--stone-soft);">No messages.</td></tr>`;
        return;
    }

    tbody.innerHTML = messages.map(m => {
        const date = new Date(m.createdAt).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
        const preview = m.message.length > 80 ? m.message.slice(0, 80) + '…' : m.message;
        return `
        <tr>
            <td>${escapeHtml(m.name)}<br><span style="font-size:0.82rem; color:var(--stone-soft);">${escapeHtml(m.email)}${m.phone ? ' · ' + escapeHtml(m.phone) : ''}</span></td>
            <td>${escapeHtml(m.subject || '—')}</td>
            <td style="max-width:260px;">${escapeHtml(preview)}</td>
            <td>
                <select onchange="updateMessageStatus(${m.id}, this.value)" style="padding:0.3rem 0.5rem; border-radius:6px; border:1px solid var(--dark-cream); font-family:inherit;">
                    ${['new', 'read', 'replied'].map(s => `<option value="${s}" ${s === m.status ? 'selected' : ''}>${s.charAt(0).toUpperCase() + s.slice(1)}</option>`).join('')}
                </select>
            </td>
            <td>${date}</td>
            <td><button class="admin-btn admin-btn-delete" onclick="deleteMessageAdmin(${m.id})">Delete</button></td>
        </tr>`;
    }).join('');
}

async function updateMessageStatus(messageId, status) {
    try {
        await apiFetch(`/api/admin/contact-messages/${messageId}`, { method: 'PUT', body: JSON.stringify({ status }) });
    } catch (err) {
        alert('Failed to update message: ' + err.message);
    }
    refreshAdminMessages();
}

async function deleteMessageAdmin(messageId) {
    if (!confirm('Delete this message?')) return;
    try {
        await apiFetch(`/api/admin/contact-messages/${messageId}`, { method: 'DELETE' });
    } catch (err) {
        alert('Failed to delete message: ' + err.message);
    }
    refreshAdminMessages();
}

// ─────────────────────────────────────────────────────────────────────────
// CHANGE (Phase 6): Admin — Newsletter tab
// ─────────────────────────────────────────────────────────────────────────
async function refreshAdminNewsletter() {
    const tbody = document.getElementById('adminNewsletterBody');
    if (!tbody) return;

    let subscribers;
    try {
        subscribers = await apiFetch('/api/admin/newsletter');
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="4">Failed to load subscribers: ${escapeHtml(err.message)}</td></tr>`;
        return;
    }

    document.getElementById('statNewsletterTotal').textContent = subscribers.length;
    document.getElementById('statNewsletterActive').textContent = subscribers.filter(s => s.isActive).length;

    if (subscribers.length === 0) {
        tbody.innerHTML = `<tr><td colspan="4" style="text-align:center; color:var(--stone-soft);">No subscribers yet.</td></tr>`;
        return;
    }

    tbody.innerHTML = subscribers.map(s => {
        const date = new Date(s.subscribedAt).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
        return `
        <tr>
            <td>${escapeHtml(s.email)}</td>
            <td><span class="status-pill ${s.isActive ? 'status-delivered' : 'status-cancelled'}">${s.isActive ? 'Active' : 'Unsubscribed'}</span></td>
            <td>${date}</td>
            <td><button class="admin-btn admin-btn-delete" onclick="deleteSubscriberAdmin(${s.id})">Delete</button></td>
        </tr>`;
    }).join('');
}

async function deleteSubscriberAdmin(subscriberId) {
    if (!confirm('Remove this subscriber?')) return;
    try {
        await apiFetch(`/api/admin/newsletter/${subscriberId}`, { method: 'DELETE' });
    } catch (err) {
        alert('Failed to delete subscriber: ' + err.message);
    }
    refreshAdminNewsletter();
}

// ─── Add / Edit Product Modal ───────────────────────────────────────────
function openAddProductModal() {
    document.getElementById('productFormTitle').textContent = 'Add New Product';
    document.getElementById('editProductId').value = '';
    document.getElementById('formName').value = '';
    document.getElementById('formCategory').value = 'bridal';
    document.getElementById('formPrice').value = '';
    document.getElementById('formBadge').value = '';
    document.getElementById('formEmoji').value = '🌸';
    document.getElementById('formFeatured').checked = false;
    document.getElementById('formBestseller').checked = false;
    document.getElementById('formNewArrival').checked = false;
    document.getElementById('formDescription').value = '';
    document.getElementById('formImageData').value = '';
    document.getElementById('imagePreview').innerHTML = `
        <span style="font-size:2.5rem;">📷</span>
        <p>Click to upload or drag & drop</p>
        <p style="font-size:0.85rem; color: var(--stone-soft);">JPG, PNG, WEBP — max 5 MB</p>`;
    document.getElementById('productFormError').style.display = 'none';
    document.getElementById('productFormModal').classList.add('active');

    document.getElementById('imageUploadArea').onclick = () =>
        document.getElementById('formImage').click();
}

function openEditProductModal(productId) {
    const cache = window.__adminProductsCache || [];
    const p = cache.find(pr => pr.id === productId);
    if (!p) return;

    document.getElementById('productFormTitle').textContent = 'Edit Product';
    document.getElementById('editProductId').value = p.id;
    document.getElementById('formName').value = p.name;
    document.getElementById('formCategory').value = p.category;
    document.getElementById('formPrice').value = p.price;
    document.getElementById('formBadge').value = p.badge || '';
    document.getElementById('formEmoji').value = p.emoji || '🌸';
    document.getElementById('formFeatured').checked = !!p.featured;
    document.getElementById('formBestseller').checked = !!p.bestseller;
    document.getElementById('formNewArrival').checked = !!p.newArrival;
    document.getElementById('formDescription').value = p.description || '';
    document.getElementById('formImageData').value = p.imageData || '';
    document.getElementById('productFormError').style.display = 'none';

    if (p.imageData) {
        document.getElementById('imagePreview').innerHTML =
            `<img src="${p.imageData}" alt="preview" style="max-width:100%;max-height:160px;border-radius:6px;object-fit:contain;">`;
    } else {
        document.getElementById('imagePreview').innerHTML = `
            <span style="font-size:2.5rem;">${p.emoji}</span>
            <p>Click to replace image</p>`;
    }

    document.getElementById('productFormModal').classList.add('active');
    document.getElementById('imageUploadArea').onclick = () =>
        document.getElementById('formImage').click();
}

function closeProductFormModal() {
    document.getElementById('productFormModal').classList.remove('active');
}

// Close modal on backdrop click
document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('productFormModal').addEventListener('click', function (e) {
        if (e.target === this) closeProductFormModal();
    });
});

// CHANGE: Video loading error handling — if a video fails to load, show the
// fallback text instead so the collection card doesn't look broken.
document.addEventListener('DOMContentLoaded', () => {
    const videos = document.querySelectorAll('.collection-video-container video');
    videos.forEach(video => {
        const fallback = video.closest('.collection-video-container').querySelector('.video-fallback');
        if (!fallback) return;

        // Show fallback if video fails to load
        video.addEventListener('error', () => {
            fallback.style.display = 'flex';
            fallback.style.alignItems = 'center';
            fallback.style.justifyContent = 'center';
        });

        // Hide fallback when video starts playing successfully
        video.addEventListener('playing', () => {
            fallback.style.display = 'none';
        });

        // Attempt to load the video — if it fails within 3 seconds, show fallback
        video.addEventListener('loadstart', () => {
            const timeout = setTimeout(() => {
                if (video.readyState === 0) { // HAVE_NOTHING state
                    fallback.style.display = 'flex';
                }
            }, 3000);
            video.addEventListener('loadeddata', () => clearTimeout(timeout), { once: true });
        });
    });
});

// ─── Image Upload Preview ───────────────────────────────────────────────
function previewUploadedImage(event) {
    const file = event.target.files[0];
    if (!file) return;

    if (file.size > 5 * 1024 * 1024) {
        alert('Image is too large. Please choose a file under 5 MB.');
        return;
    }

    const reader = new FileReader();
    reader.onload = (e) => {
        const dataUrl = e.target.result;
        document.getElementById('formImageData').value = dataUrl;
        document.getElementById('imagePreview').innerHTML =
            `<img src="${dataUrl}" alt="preview" style="max-width:100%;max-height:160px;border-radius:6px;object-fit:contain;">`;
    };
    reader.readAsDataURL(file);
}

// ─── Save Product (Add or Update) ──────────────────────────────────────
async function saveProduct() {
    const errEl = document.getElementById('productFormError');
    const name        = document.getElementById('formName').value.trim();
    const category    = document.getElementById('formCategory').value;
    const priceRaw    = document.getElementById('formPrice').value;
    const badge       = document.getElementById('formBadge').value;
    const emoji       = document.getElementById('formEmoji').value.trim() || '🌸';
    const featured    = document.getElementById('formFeatured').checked;
    const bestseller  = document.getElementById('formBestseller').checked;
    const newArrival  = document.getElementById('formNewArrival').checked;
    const description = document.getElementById('formDescription').value.trim();
    const imageData   = document.getElementById('formImageData').value;
    const editId      = document.getElementById('editProductId').value;

    errEl.style.display = 'none';

    if (!name || !category || !priceRaw || !description) {
        errEl.textContent = 'Please fill in all required fields.';
        errEl.style.display = 'block';
        return;
    }

    const price = parseInt(priceRaw, 10);
    if (isNaN(price) || price < 0) {
        errEl.textContent = 'Please enter a valid price.';
        errEl.style.display = 'block';
        return;
    }

    const payload = { name, category, price, badge, emoji, featured, bestseller, newArrival, description, imageData };

    try {
        if (editId) {
            await apiFetch(`/api/admin/products/${editId}`, { method: 'PUT', body: JSON.stringify(payload) });
            showNotification('Product updated successfully!');
        } else {
            await apiFetch('/api/admin/products', { method: 'POST', body: JSON.stringify(payload) });
            showNotification('Product added successfully!');
        }
    } catch (err) {
        errEl.textContent = err.message;
        errEl.style.display = 'block';
        return;
    }

    closeProductFormModal();
    await refreshAdminTable();
    await loadProducts();
}

// ─── Delete Product ─────────────────────────────────────────────────────
async function deleteProduct(productId) {
    const cache = window.__adminProductsCache || [];
    const p = cache.find(pr => pr.id === productId);
    if (!p) return;
    if (!confirm(`Delete "${p.name}"? This cannot be undone.`)) return;

    try {
        await apiFetch(`/api/admin/products/${productId}`, { method: 'DELETE' });
    } catch (err) {
        alert('Failed to delete product: ' + err.message);
        return;
    }

    await refreshAdminTable();
    await loadProducts();
    showNotification('Product deleted.');
}

// ─── Export CSV ─────────────────────────────────────────────────────────
function exportCSV() {
    const list = window.__adminProductsCache || products;
    const headers = ['ID', 'Name', 'Category', 'Price', 'Badge', 'Emoji', 'Description'];
    const rows = list.map(p => [
        p.id,
        `"${p.name.replace(/"/g, '""')}"`,
        p.category,
        p.price,
        p.badge || '',
        p.emoji || '',
        `"${(p.description || '').replace(/"/g, '""')}"`
    ]);

    const csvContent = [headers.join(','), ...rows.map(r => r.join(','))].join('\n');
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    downloadBlob(blob, 'jeevani_products.csv');
    showNotification('CSV exported!');
}

// ─── Export JSON ─────────────────────────────────────────────────────────
function exportJSON() {
    const list = window.__adminProductsCache || products;
    const exportData = list.map(({ imageData, ...rest }) => rest);
    const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
    downloadBlob(blob, 'jeevani_products.json');
    showNotification('JSON exported!');
}

function downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

// ═══════════════════════════════════════════════════════════════════════
// END: Product Management System
// ═══════════════════════════════════════════════════════════════════════

document.addEventListener("keydown", function(event) {
    // Press F9 to open Admin Panel
    if (event.key === "F9") {
        window.location.href = "admin.html";
    }
});

