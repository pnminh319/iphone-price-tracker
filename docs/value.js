
(function () {

    "use strict";

    const VERSION = "V5.3";

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

    const normalize = value =>
        String(value ?? "")
            .trim()
            .toLowerCase();

    const clamp = value =>
        Math.max(0, Math.min(100, value));

    const round = value =>
        Math.round(value * 100) / 100;

    function getSegment(price) {

        price = Number(price);

        if (!(price > 0)) return "UNKNOWN";
        if (price < 10000000) return "Dưới 10 triệu";
        if (price < 15000000) return "10–15 triệu";
        if (price < 20000000) return "15–20 triệu";
        if (price < 25000000) return "20–25 triệu";
        if (price < 30000000) return "25–30 triệu";
        if (price < 40000000) return "30–40 triệu";

        return "Từ 40 triệu";

    }

    function qualityScore(
        model,
        specs,
        profile = "balanced"
    ) {

        const database = specs.models || specs;

        const item = database[
            normalize(model)
        ];

        if (!item) return null;

        const weights =
            PROFILES[profile] ||
            PROFILES.balanced;

        let total = 0;

        for (const key of Object.keys(weights)) {

            const value = Number(item[key]);

            if (
                item[key] == null ||
                !Number.isFinite(value) ||
                value < 0 ||
                value > 100
            ) {
                return null;
            }

            total += value * weights[key];

        }

        return round(total / 100);

    }

    function valueRatio(quality, price) {

        if (
            quality == null ||
            !(Number(price) > 0)
        ) return null;

        return round(
            quality / (Number(price) / 1000000)
        );

    }

    function isFresh(row, hours = 12) {

        const timestamp = Date.parse(
            row.timestamp
        );

        const age = Date.now() - timestamp;

        const stock = normalize(
            row.stock
        ).replaceAll("_", "");

        return (
            ["instock", "available"].includes(stock) &&
            Number(row.price) > 0 &&
            Number.isFinite(age) &&
            age >= 0 &&
            age <= hours * 3600000
        );

    }

    function median(values) {

        if (!values.length) return null;

        const sorted = [...values].sort(
            (a, b) => a - b
        );

        const mid = Math.floor(
            sorted.length / 2
        );

        return sorted.length % 2
            ? sorted[mid]
            : (sorted[mid - 1] + sorted[mid]) / 2;

    }

    function groupKey(row) {

        return [
            row.model,
            row.storage,
            row.color || "UNKNOWN"
        ].map(normalize).join("|");

    }

    function dealScore(price, marketMedian) {

        if (
            !(price > 0) ||
            !(marketMedian > 0)
        ) return null;

        return round(clamp(
            50 +
            (marketMedian - price)
            / marketMedian * 250
        ));

    }

    function advertisedDiscount(row) {

        const price = Number(row.price);

        const original = Number(
            row.original_price
        );

        if (!(original > price && price > 0)) {
            return null;
        }

        return round(
            (original - price)
            / original * 100
        );

    }

    function rankProducts(
        rows,
        specs,
        options = {}
    ) {

        const profile =
            options.profile || "balanced";

        const budget =
            Number(options.budget) || Infinity;

        const eligible = rows.filter(row =>
            isFresh(row) &&
            Number(row.price) <= budget
        );

        const groups = new Map();

        for (const row of rows.filter(r => isFresh(r))) {

            const key = groupKey(row);

            if (!groups.has(key)) {
                groups.set(key, new Map());
            }

            const shops = groups.get(key);

            const price = Number(row.price);

            const previous = shops.get(
                row.retailer
            );

            if (
                previous == null ||
                price < previous
            ) {
                shops.set(row.retailer, price);
            }

        }

        const result = eligible.map(row => {

            const quality = qualityScore(
                row.model,
                specs,
                profile
            );

            const ratio = valueRatio(
                quality,
                row.price
            );

            const prices = [
                ...(groups.get(
                    groupKey(row)
                ) || new Map()).values()
            ];

            const marketMedian =
                prices.length >= 2
                ? median(prices)
                : null;

            return {

                ...row,

                quality_score: quality,

                value_ratio: ratio,

                value_score: null,

                segment: getSegment(row.price),

                market_median: marketMedian,

                compared_retailers: prices.length,

                deal_score: dealScore(
                    Number(row.price),
                    marketMedian
                ),

                advertised_discount:
                    advertisedDiscount(row)

            };

        });

        const bestBySegment = new Map();

        for (const row of result) {

            if (row.value_ratio == null) continue;

            const best =
                bestBySegment.get(row.segment) || 0;

            bestBySegment.set(
                row.segment,
                Math.max(best, row.value_ratio)
            );

        }

        for (const row of result) {

            const best = bestBySegment.get(
                row.segment
            );

            if (
                best > 0 &&
                row.value_ratio != null
            ) {

                row.value_score = round(
                    row.value_ratio / best * 100
                );

            }

        }

        return result.sort(
            (a, b) =>
                (b.value_score ?? -1) -
                (a.value_score ?? -1) ||
                (b.value_ratio ?? -1) -
                (a.value_ratio ?? -1)
        );

    }

    window.iPhoneValue = {

        VERSION,
        PROFILES,
        getSegment,
        qualityScore,
        valueRatio,
        isFresh,
        dealScore,
        advertisedDiscount,
        rankProducts

    };

})();
