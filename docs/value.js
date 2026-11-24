
/* iPhone Price Tracker V5
   Value Score Engine
*/

(function () {
    "use strict";

    const VERSION = "5.0";

    const PROFILES = {
        balanced: {
            performance: 25,
            camera: 20,
            battery: 20,
            display: 15,
            longevity: 20
        },

        gaming: {
            performance: 40,
            camera: 5,
            battery: 25,
            display: 20,
            longevity: 10
        },

        camera: {
            performance: 15,
            camera: 40,
            battery: 15,
            display: 15,
            longevity: 15
        },

        longterm: {
            performance: 20,
            camera: 10,
            battery: 25,
            display: 10,
            longevity: 35
        }
    };

    function number(value) {
        if (
            value === null ||
            value === undefined ||
            value === ""
        ) return null;

        const n = Number(value);

        return Number.isFinite(n) ? n : null;
    }

    function clamp(value, min = 0, max = 100) {
        return Math.max(min, Math.min(max, value));
    }

    function round(value) {
        return Math.round(value * 10) / 10;
    }

    function normalizeModel(model) {
        return String(model || "")
            .toLowerCase()
            .trim()
            .replace(/\s+/g, " ");
    }

    function normalizeStorage(storage) {
        return String(storage || "")
            .toUpperCase()
            .replace(/\s+/g, "");
    }

    function normalizeColor(color) {
        return String(color || "")
            .toLowerCase()
            .trim();
    }

    function groupKey(product) {
        return [
            normalizeModel(product.model),
            normalizeStorage(product.storage),
            normalizeColor(product.color)
        ].join("|");
    }

    // =====================================
    // QUALITY SCORE
    // =====================================

    function qualityScore(
        specs,
        profile = "balanced"
    ) {
        if (!specs) return null;

        const weights =
            PROFILES[profile] || PROFILES.balanced;

        let score = 0;

        for (const [key, weight] of
            Object.entries(weights)) {

            const value = number(specs[key]);

            if (
                value === null ||
                value < 0 ||
                value > 100
            ) {
                return null;
            }

            score += value * weight / 100;
        }

        return round(score);
    }

    // =====================================
    // VALUE PER MILLION VND
    // =====================================

    function valueRatio(quality, price) {
        const q = number(quality);
        const p = number(price);

        if (
            q === null ||
            p === null ||
            p <= 0
        ) return null;

        return q / (p / 1000000);
    }

    // =====================================
    // MEDIAN MARKET PRICE
    // =====================================

    function median(values) {
        const sorted = values
            .map(number)
            .filter(v => v !== null && v > 0)
            .sort((a, b) => a - b);

        if (!sorted.length) return null;

        const mid = Math.floor(sorted.length / 2);

        return sorted.length % 2
            ? sorted[mid]
            : (sorted[mid - 1] + sorted[mid]) / 2;
    }

    // =====================================
    // DEAL SCORE
    // 50 = market median
    // =====================================

    function dealScore(price, marketPrice) {
        const p = number(price);
        const m = number(marketPrice);

        if (
            p === null ||
            m === null ||
            p <= 0 ||
            m <= 0
        ) return null;

        const difference = (m - p) / m;

        return round(
            clamp(50 + difference * 250)
        );
    }

    // =====================================
    // ADVERTISED DISCOUNT
    // =====================================

    function advertisedDiscount(price, original) {
        const p = number(price);
        const o = number(original);

        if (
            p === null ||
            o === null ||
            p <= 0 ||
            o <= p
        ) return null;

        return {
            percent: round((o - p) / o * 100),
            saving: o - p
        };
    }

    // =====================================
    // STOCK AND FRESHNESS
    // =====================================

    function eligible(product, maxAgeHours) {
        const stock = String(
            product.stock || ""
        ).toUpperCase();

        if (![
            "IN_STOCK",
            "INSTOCK",
            "AVAILABLE"
        ].includes(stock)) {
            return false;
        }

        const time = Date.parse(product.timestamp);

        if (!Number.isFinite(time)) return false;

        const age = (Date.now() - time) / 3600000;

        return age >= 0 && age <= maxAgeHours;
    }

    // =====================================
    // RANK PRODUCTS
    // =====================================

    function rankProducts(
        products,
        specs,
        options = {}
    ) {
        const {
            profile = "balanced",
            minPrice = 0,
            maxPrice = Infinity,
            maxAgeHours = 12
        } = options;

        const market = new Map();

        const validProducts = products.filter(p =>
            eligible(p, maxAgeHours) &&
            number(p.price) > 0
        );

        // Group identical variants.
        for (const product of validProducts) {
            const key = groupKey(product);

            if (!market.has(key)) {
                market.set(key, new Map());
            }

            const retailers = market.get(key);

            const retailer = String(
                product.retailer || ""
            ).toUpperCase();

            const previous = retailers.get(retailer);

            if (
                !previous ||
                Date.parse(product.timestamp) >
                Date.parse(previous.timestamp)
            ) {
                retailers.set(retailer, product);
            }
        }

        const ranked = [];

        for (const retailers of market.values()) {
            const offers = [...retailers.values()];

            const marketPrice = offers.length >= 2
                ? median(offers.map(p => p.price))
                : null;

            for (const product of offers) {
                const price = number(product.price);

                if (
                    price < minPrice ||
                    price > maxPrice
                ) continue;

                const model = normalizeModel(
                    product.model
                );

                const quality = qualityScore(
                    specs[model],
                    profile
                );

                const ratio = valueRatio(
                    quality,
                    price
                );

                if (ratio === null) continue;

                ranked.push({
                    ...product,

                    quality_score: quality,

                    value_ratio: ratio,

                    market_price: marketPrice,

                    retailer_count: offers.length,

                    deal_score: dealScore(
                        price,
                        marketPrice
                    ),

                    advertised_discount:
                        advertisedDiscount(
                            price,
                            product.original_price
                        )
                });
            }
        }

        ranked.sort(
            (a, b) =>
                b.value_ratio - a.value_ratio ||
                a.price - b.price
        );

        const bestRatio =
            ranked[0]?.value_ratio || 0;

        return ranked.map((product, index) => ({
            ...product,

            rank: index + 1,

            value_score: bestRatio > 0
                ? round(
                    product.value_ratio /
                    bestRatio * 100
                )
                : null
        }));
    }

    // =====================================
    // PRICE SEGMENTS
    // =====================================

    function getSegment(price) {
        const p = number(price);

        if (p === null || p <= 0) return null;

        if (p < 10000000) return "Dưới 10 triệu";
        if (p < 15000000) return "10–15 triệu";
        if (p < 20000000) return "15–20 triệu";
        if (p < 25000000) return "20–25 triệu";
        if (p < 30000000) return "25–30 triệu";
        if (p < 40000000) return "30–40 triệu";

        return "Trên 40 triệu";
    }

    window.iPhoneValue = {
        VERSION,
        PROFILES,
        qualityScore,
        valueRatio,
        dealScore,
        advertisedDiscount,
        rankProducts,
        getSegment
    };

})();
