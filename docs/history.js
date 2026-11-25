(function () {
    "use strict";

    const VERSION = "V5.4.2.3";
    const DAY = 86400000;

    const RETAILERS = [
        "TGDD", "FPT", "CPS",
        "HHM", "DDV", "VTS", "SDK"
    ];

    const PERIODS = [7, 14, 30, 60, 90];

    const norm = value =>
        String(value ?? "")
            .normalize("NFC")
            .trim()
            .toLowerCase()
            .replace(/\s+/g, " ");

    function storageKey(value) {
        const text = norm(value).replace(/\s/g, "");

        if (text === "1024gb") return "1tb";
        if (text === "2048gb") return "2tb";

        return text;
    }

    function productKey(row) {
        return [
            norm(row.model),
            storageKey(row.storage)
        ].join("|");
    }

    function scope(row) {
        return String(
            row.scope ||
            (
                !row.color ||
                norm(row.color) === "unknown"
                    ? "PRODUCT"
                    : "COLOR"
            )
        ).toUpperCase();
    }

    function dayKey(timestamp) {
        return new Intl.DateTimeFormat("sv-SE", {
            timeZone: "Asia/Ho_Chi_Minh",
            year: "numeric",
            month: "2-digit",
            day: "2-digit"
        }).format(new Date(timestamp));
    }

    function median(values) {
        if (!values.length) return null;

        const sorted = [...values].sort(
            (a, b) => a - b
        );

        const mid = Math.floor(sorted.length / 2);

        return sorted.length % 2
            ? sorted[mid]
            : (sorted[mid - 1] + sorted[mid]) / 2;
    }

    function validPrice(row) {
        const price = Number(row.price);

        const stock = norm(row.stock)
            .replace(/[\s_-]/g, "");

        return (
            Number.isFinite(price) &&
            price > 0 &&
            ["instock", "available"].includes(stock)
        );
    }

    function matches(row, selected) {
        if (
            productKey(row) !== productKey(selected)
        ) return false;

        if (scope(row) !== scope(selected)) {
            return false;
        }

        if (scope(selected) === "COLOR") {
            return norm(row.color) === norm(selected.color);
        }

        return true;
    }

    function getHistory(
        history,
        selected,
        options = {}
    ) {
        const requestedDays = Number(options.days || 90);

        const days = PERIODS.includes(requestedDays)
            ? requestedDays
            : 90;

        const end = Number(options.now ?? Date.now());
        const start = end - days * DAY;

        const retailers =
            options.retailers || RETAILERS;

        const mode = options.mode || "hour";

        const unique = new Map();

        for (const row of history || []) {

            const retailer = String(
                row.retailer || ""
            ).toUpperCase();

            const timestamp = Date.parse(row.timestamp);

            if (!retailers.includes(retailer)) continue;

            if (!matches(row, selected)) continue;

            if (row.source === "BACKFILL") {
                if (mode !== "day" || !Number.isFinite(Number(row.price)) || Number(row.price) <= 0) continue;
            } else if (!validPrice(row)) continue;

            if (!Number.isFinite(timestamp)) continue;

            if (timestamp < start || timestamp > end) {
                continue;
            }

            const record = {
                ...row,
                retailer,
                price: Number(row.price),
                time: timestamp,
                day: dayKey(timestamp),
                source: row.source || "CRAWLER"
            };

            const key = [
                retailer,
                productKey(row),
                scope(row),
                norm(row.color || "UNKNOWN"),
                timestamp
            ].join("|");

            const previous = unique.get(key);

            if (
                !previous ||
                record.source === "CRAWLER"
            ) {
                unique.set(key, record);
            }
        }

        let rows = [...unique.values()].sort(
            (a, b) => a.time - b.time
        );

        if (mode === "day") {

            const daily = new Map();

            for (const row of rows) {

                const key = [
                    row.retailer,
                    row.day
                ].join("|");

                if (!daily.has(key) || row.source !== "BACKFILL" || daily.get(key).source === "BACKFILL") daily.set(key, row);
            }

            rows = [...daily.values()].sort(
                (a, b) => a.time - b.time
            );
        }

        return rows;
    }

    function dailyPrices(rows) {

        const daily = new Map();

        const sorted = [...rows].sort(
            (a, b) => a.time - b.time
        );

        for (const row of sorted) {

            const key = [
                row.retailer,
                row.day
            ].join("|");

            if (!daily.has(key) || row.source !== "BACKFILL" || daily.get(key).source === "BACKFILL") daily.set(key, row);
        }

        return [...daily.values()];
    }

    function statistics(rows) {

        if (!rows.length) return null;

        const sorted = [...rows].sort(
            (a, b) => a.time - b.time
        );

        const prices = sorted.map(
            row => row.price
        );

        const daily = dailyPrices(sorted);

        const dailyValues = daily.map(
            row => row.price
        );

        const low = prices.reduce(
            (a, b) => Math.min(a, b)
        );

        const high = prices.reduce(
            (a, b) => Math.max(a, b)
        );

        const average = dailyValues.reduce(
            (sum, price) => sum + price,
            0
        ) / dailyValues.length;

        return {
            count: sorted.length,

            observed_days: new Set(
                sorted.map(row => row.day)
            ).size,

            lowest: low,
            highest: high,
            average: Math.round(average),
            median: median(dailyValues),

            first_price: sorted[0].price,

            latest_price:
                sorted[sorted.length - 1].price,

            first_timestamp:
                sorted[0].timestamp,

            latest_timestamp:
                sorted[sorted.length - 1].timestamp,

            lowest_timestamp:
                sorted.find(
                    row => row.price === low
                ).timestamp
        };
    }

    function compareRetailers(
        history,
        selected,
        options = {}
    ) {

        const rows = getHistory(
            history,
            selected,
            {
                ...options,
                mode: options.mode || "hour"
            }
        );

        return RETAILERS.map(retailer => {

            const records = rows.filter(
                row => row.retailer === retailer
            );

            return {
                retailer,
                rows: records,
                stats: statistics(records)
            };

        });
    }

    function historicalDeal(
        currentPrice,
        rows,
        options = {}
    ) {

        const price = Number(currentPrice);

        const minDays = Number(
            options.minDays || 14
        );

        if (!(price > 0)) return null;

        const stats = statistics(rows.filter(r => r.source !== "BACKFILL"));

        if (!stats) return null;

        const reference = stats.median;

        const discount =
            (reference - price) / reference * 100;

        const aboveLowest =
            price - stats.lowest;

        const sufficient =
            stats.observed_days >= minDays;

        let score = null;
        let label = "CHƯA ĐỦ LỊCH SỬ";

        if (sufficient) {

            score = Math.round(
                Math.max(
                    0,
                    Math.min(
                        100,
                        50 + discount * 2.5
                    )
                ) * 100
            ) / 100;

            if (price <= stats.lowest) {
                label = "CHẠM HOẶC THẤP HƠN ĐÁY";
            } else if (discount >= 10) {
                label = "GIÁ TỐT";
            } else if (discount >= 5) {
                label = "ĐANG GIẢM";
            } else if (discount >= 0) {
                label = "GIÁ BÌNH THƯỜNG";
            } else {
                label = "CAO HƠN TRUNG VỊ";
            }
        }

        return {
            historical_deal_score: score,

            historical_discount_pct:
                Math.round(discount * 100) / 100,

            historical_median: reference,

            historical_lowest: stats.lowest,

            historical_highest: stats.highest,

            above_lowest: aboveLowest,

            observed_days: stats.observed_days,

            sufficient_history: sufficient,

            label
        };
    }

    function coverage(rows, days = 90) {

        const observed = new Set(
            rows.map(row => row.day)
        ).size;

        return {
            requested_days: days,
            observed_days: observed,

            coverage_pct: Math.min(
                100,
                Math.round(observed / days * 100)
            ),

            first_timestamp:
                rows.length
                    ? new Date(
                        rows.reduce(
                            (min, row) =>
                                Math.min(min, row.time),
                            Infinity
                        )
                    ).toISOString()
                    : null,

            last_timestamp:
                rows.length
                    ? new Date(
                        rows.reduce(
                            (max, row) =>
                                Math.max(max, row.time),
                            -Infinity
                        )
                    ).toISOString()
                    : null
        };
    }

    window.iPhoneHistory = {
        VERSION,
        RETAILERS,
        PERIODS,
        productKey,
        scope,
        dayKey,
        median,
        validPrice,
        matches,
        getHistory,
        statistics,
        compareRetailers,
        historicalDeal,
        coverage
    };

})();
