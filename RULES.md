# Rules and guidelines

This document describes exactly what the app does: how it builds the list of stocks, how the screener decides what to show, how the backtest buys and sells, and how its results are measured. It is the same text as the **Rules** tab of the app.

**How this document stays current.** It is the single place the rules are written down; the Rules tab displays this file, so one edit updates both GitHub and the website. The **Parameters** section near the end is generated from the code itself, so its numbers cannot drift from what the app really uses. An automatic check on GitHub fails if that section no longer matches the code, and warns when strategy code changes without this file changing. Every change to the logic is recorded in the **Change log** at the end.

Rules describe what the code does. **Guidelines** (section 8) are recommendations for using the results with real money; the code does not enforce them.

---

## 1. Stock universe

**1.1 Index lists.** Each run starts from one of eleven universes: ten Nifty indices and the Nasdaq 100 (see Parameters and 1.5). The list of member stocks is downloaded from NSE Indices' official constituent files, trying niftyindices.com and then nsearchives.nseindia.com. Only stocks in the EQ, BE and BZ series are kept. Lists are kept for 24 hours.

**1.2 If an index file cannot be downloaded.** Two indices are rebuilt from others, using NSE's own definitions:
- Nifty Total Market = Nifty 500 + Nifty Microcap 250.
- Nifty Midcap 150 = Nifty LargeMidcap 250 minus Nifty 100.

Any other index that fails to download stops the run with a message.

**1.3 Your own list.** An uploaded CSV replaces the index completely. It needs a `Symbols` (or `Symbol`) column; `Sector` (or `Industry`) and `ISIN` (or `ISIN Code`) are optional. Symbols written as `NSE:TCS`, `TCS.NS` or `tcs` are all read as `TCS`. In the backtest's "Compare all indices", uploaded lists are not used.
- The list is read as stocks of the market of the index selected next to it: with an Indian index selected it is a list of NSE stocks; with the Nasdaq 100 selected it is a list of US stocks (symbols such as `AAPL`, `NASDAQ:AAPL` or `brk.b`, read as `AAPL` and `BRK-B`), measured against the S&P 500 and shown in dollars.

**1.4 Lending companies (switch, on by default).** When the switch is on, lenders are removed from the universe before anything else happens.
- A company is a lender when NSE Indices' **Basic Industry** (the most detailed level of NSE's industry classification, which describes the company's core business) is Public Sector Bank, Private Sector Bank, Other Bank (small finance, payment and foreign banks), Non Banking Financial Company (NBFC), Housing Finance Company or Financial Institution.
- Insurers, fund houses, brokers, exchanges, depositories, rating agencies, fintech, holding companies and investment companies are **not** lenders under this rule and stay in.
- The Basic Industry is read from NSE's quote data, only for stocks whose broad sector is Financial Services, and kept for 30 days.
- If NSE does not answer, these fallback rules decide instead, and the results say so: "Bank" in the company name; membership of NSE's Nifty NBFC index; housing-finance or lending words in the company name (excluding names with insurance, asset management, holding, exchange, depository, registrar, broking or securities); a short list of government financial institutions (see Parameters).
- **And all other financial companies (tick box; ticked by default in the Backtest tab, unticked in the Screener).** With it ticked, every company in NSE's Financial Services sector is left out, not only lenders: insurers, fund houses, brokers, exchanges, depositories, rating agencies, fintech, holding and investment companies too. It works in both tabs and in the start-month test (6.2).
- Your **Always exclude** and **Never exclude** symbol lists override everything above. They are remembered in your browser. The Always exclude list removes any stock you name, financial or not (for example `DIACABS, CUPID`).
- Every run lists the companies left out and the other financial companies kept, with the source of each decision.
- For US stocks the industry in the stock list decides instead (for the Nasdaq 100, its ICB industry and subsector): a stock is a lender when that industry names a bank, a consumer or commercial lender, a mortgage lender or mortgage REIT, or a savings institution. The Nasdaq-100's own rules already leave out financial companies, so for that index the filter normally removes nothing; it matters for an uploaded US list. The Always and Never exclude lists work the same way.

**1.5 Nasdaq 100 (US stocks).** The member list comes from Nasdaq's own website (the index operator). Each stock's industry comes from Wikipedia's Nasdaq-100 table (ICB industry and subsector). If Nasdaq does not answer, Wikipedia's table is used as the list; if neither answers, a snapshot saved in the repository (`data/nasdaq100.csv`, dated in Parameters) is used. The results say which source was used. Symbols are kept in Yahoo Finance's form (a class letter after a dot becomes a dash, `BRK.B` becomes `BRK-B`). For this universe everything that depends on the market changes together:
- relative strength is measured against the **S&P 500** price index instead of the Nifty 50 (2.1);
- the backtest benchmark is the **S&P 500 Total Return Index**, or the S&P 500 price index if that cannot be fetched (4.10);
- a day's prices count as final from **8 pm New York time** (about 6:30 am IST the next day, 5:30 am during US summer time), and a month is complete from that moment on its last weekday (2.7);
- money is in **US dollars**, with a default starting capital of $10,000; the order panel's quantities are whole US shares;
- Indian and US prices are stored separately, so a symbol that exists on both markets is never mixed up.
All other rules (sections 2 to 5 and 7) are the same as for the Nifty indices.

---

## 2. Definitions used everywhere

**2.1 Relative strength (RS).** A stock's month-end closing price divided by the Nifty 50 price index's month-end close (for the Nasdaq 100 and US lists: the S&P 500 price index's). Stock prices are adjusted for splits and dividends; the index is not, which slightly favours high-dividend stocks.

**2.2 RS ratio ("RS of high", shown as a percentage).** This month's RS divided by the highest month-end RS of the last 12 months, the current month included. 100% means RS is at a new 12-month high; 97% means it is 3% below its 12-month high.
- The screener needs at least 13 month-ends of history for a stock (the current one plus 12 before it); the backtest needs 12. This only matters for recently listed stocks.

**2.3 12-month EMA.** The exponential moving average of month-end closes with a span of 12 months (smoothing factor 2/13), started from the first available month.

**2.4 Closing range (CR).** Where the month's close sits between the month's low and high: (close − low) ÷ (high − low). 100% means it closed at the month's high. If high equals low, CR is 0%.

**2.5 Monthly return (MR).** This month's close ÷ last month's close − 1.

**2.6 Month labels.** In the backtest, a month means the month the stocks were **held**. "Oct 2026" is the portfolio chosen at the September close, bought at October's first open and valued at the October close.

**2.7 When a month is complete.** A month counts as complete from 5 pm IST on its last weekday (US stocks: from 8 pm New York time on its last weekday). Before that, its numbers are provisional. A month whose last weekday is a market holiday completes a day late, which is safe. Because the US closes later, for a few hours after an Indian month-end the latest completed month of a US universe is still the month before; the end month is moved back if needed, and the results say so.

---

## 3. Screener rules

**3.1 Prices.** Three years of monthly prices from Yahoo Finance, adjusted for splits and dividends. If Yahoo returns two rows for one month, they are merged (highest high, lowest low, latest close).

**3.2 Latest-month filters.** A stock is considered only if, on the **latest month in the data**, it passes all switched-on filters:
- close at or above its 12-month EMA (switch);
- closing range at or above the minimum;
- monthly return at or above the minimum.

The latest month is usually the running month, so these filters are provisional until the month ends.

**3.3 The month cells.** For each of the months shown, the stock's RS ratio is computed:
- 100%: a new 12-month RS high (dark cell, shown as 100);
- at or above the RS threshold but below 100%: a light cell, shown rounded down (for example 98);
- below the threshold: shown as the RS ratio in grey, not marked.

**3.4 Who is listed.** A stock is listed if it passes the latest-month filters **and** is marked in at least one of the months shown. It does not have to be marked in the latest month; use "Marked in [latest month] only" to see those.

**3.5 Weekly drill-down (switch, off by default).** Keeps only listed stocks whose latest week's return is above 4% **and** whose latest week's closing range is above 75% (strictly above). The latest day's closing range and change are shown for information only; they do not filter.

**3.6 Run, Close in range and the RS line.** "Run" counts consecutive marked months ending at the latest month. "Close in range" is the latest month's closing range. The RS line shows 24 months, starting at 100.

---

## 4. Backtest rules

The backtest decides at each **month-end close** and trades at the **first open of the next month**.

**4.1 Prices.** Daily prices from Yahoo Finance from 2010, adjusted for splits and dividends, kept on the server so later runs only fetch new days. Before use:
- zero or negative prices are removed;
- a one-day move of 1.6× (or 1/1.6) that fully reverses the next day is treated as a bad price and removed;
- each day's high and low are widened to include its open and close.

Daily prices are then turned into monthly candles: open = the month's first open, high = highest high, low = lowest low, close = last close. A month with no close is missing for that stock. Only completed months are used (2.7).

**Where the prices are kept.** The website's server forgets its stored prices whenever Streamlit Cloud puts the app to sleep or restarts it. So a copy is kept on GitHub:
- A scheduled GitHub job (`.github/workflows/price-snapshot.yml`) runs on weekdays at 20:00 IST and 08:00 IST (and can be started by hand). It downloads the daily prices of every Nifty Total Market stock (or, if that list is not available, Nifty 500 and Nifty Microcap 250) and every Nasdaq 100 stock from Yahoo Finance, using the same cleaning and the same files as the website, and saves them as `prices_in.tar.gz` and `prices_us.tar.gz` on the repository's `price-store` release. Each run starts from the previous file, so it only asks Yahoo for new days.
- The job does not publish a file in which fewer than 90% of the stocks have prices, so a bad Yahoo day cannot replace a good file.
- When the website starts with no stored prices, it downloads that file once (a few seconds) and then asks Yahoo only for the days after it. If the file cannot be downloaded, prices come from Yahoo as before; nothing else changes.
- The saved file holds the same Yahoo prices the website would download itself; it changes speed, not results.

**Holidays and late data.** When at least half of the stocks that were fetched after the day's cutoff still lack that day's close (a market holiday the app does not know about, or Yahoo publishing late), the app asks Yahoo for 5 of them first. If none has the day yet, it waits 20 minutes before asking again instead of downloading every stock again. If any has it, all the missing stocks are fetched. When only a few stocks lack the day, they are fetched directly, as before.

**4.2 Buy rule (checked at each month-end, for stocks not already held).** The backtest has two entry rules to choose from: the **RS rule** below (the default) and **Setup A + second chance** (4.12). With the RS rule, a stock qualifies when all of these are true:
1. its RS ratio is 100%, **or** its RS ratio is at least the **buy level** **and** higher than its RS ratio two month-ends earlier;
2. close at or above its 12-month EMA (if the EMA switch is on);
3. closing range at or above the minimum;
4. monthly return at or above the minimum;
5. a price exists at the next month's first open.

The **buy level** ("Buy at RS % or more") is the RS threshold unless you set a higher one. Set it to 100 to buy only stocks at a new 12-month RS high; the "higher than two month-ends earlier" path then never applies. It cannot be below the RS threshold (a lower value is replaced by the threshold), because such a stock would be sold at the next month-end. The sell rule (4.4) always uses the RS threshold, not the buy level. Setup A + second chance (4.12) does not use the buy level.

**4.3 Ranking.** If more stocks qualify than there are empty slots, they are sorted by the chosen ranking (closing range by default; or 6-month or 12-month price return), highest first; ties are broken by the higher RS ratio, then the higher monthly return, then the symbol in alphabetical order. The top ones fill the empty slots.

**4.4 Sell rule (checked at each month-end, for stocks held).** A stock is sold at the next month's first open when, at the month-end, any of these is true (the first one found is the reason shown):
1. it has no closing price that month ("Price data stopped");
2. its RS ratio is below the RS threshold (not the buy level), or cannot be computed ("RS below threshold");
3. its close is below its 12-month EMA, if the EMA switch is on ("Close below 12-month EMA").

On top of these, the market breadth rule (4.13) can sell every holding at once.

A held stock is **never swapped** for a better candidate. It stays until a sell rule fires.

**4.5 Trading at the next open.**
- Sells first: proceeds = shares × the first open × (1 − cost). If there is no opening price, the last close is used.
- Portfolio value at the open = cash + held shares at the opening price.
- Each new stock gets **1/slots of that value**, paid from cash. If less cash is left, it gets what is left; if that is less than 20% of a slot's money, the stock is not bought ("Not bought for lack of cash").
- Shares bought = amount × (1 − cost) ÷ the first open. Part-shares are allowed.
- Holdings are **not rebalanced**: winners keep growing; nothing is trimmed.

**4.6 Cash.** Empty slots and leftover money stay in cash, which earns the cash yield (the yearly rate converted to a monthly rate) every month.

**4.7 Valuation.** The portfolio is valued at each month-end close (a stock without a close that month is valued at its last close).

**4.8 Period.** Start month from January 2012; end month at most the last completed month. The portfolio starts in cash at the close of the month before the start month.

**4.9 Trade records.** A trade's return includes the cost on both sides. Months held = sell month − buy month. Positions still open at the end are valued at the last close without a selling cost and marked "Still held".

**4.10 Benchmark.** The Nifty 50 Total Return Index from niftyindices.com; if that is not available, the Nifty 50 price index (the report says which was used). For the Nasdaq 100 and US lists: the S&P 500 Total Return Index from Yahoo Finance (`^SP500TR`), or the S&P 500 price index (`^GSPC`) if that is not available.

**4.11 Path dependence.** Because held stocks are never swapped, the holdings in any month depend on the start month. Two backtests with different start months can hold different stocks in the same month.

**4.12 Entry rule "Setup A + second chance".** An alternative to the RS rule in 4.2; ranking (4.3), the sell rule (4.4) and everything else stay the same. Both signals depend only on each stock's own prices, never on what the portfolio holds or whether slots were full.

- **Setup A, first close near the 12-month high.** At a month-end a stock qualifies when all of these are true:
  1. its close is at least the near-high level (default **95%**) of its highest month-end close over the last 12 months (this month included; a new 12-month high counts);
  2. that was not true at any of the previous *Q* month-ends (*Q* = "first time in", default **12**), so a stock needs 12 + *Q* months of prices;
  3. RS ratio at least the minimum RS threshold;
  4. close at or above its 12-month EMA (if the EMA switch is on), closing range and monthly return at or above their minimums;
  5. a price exists at the next month's first open.
- **Second chance (after a pause).** Because Setup A can happen only once in *Q* months, a stock missed when slots were full would otherwise be lost for a year. Within the second-chance window (default **12** months) after its Setup A month, a stock qualifies again at a month-end when:
  1. at some month-end since its Setup A (or since its last second chance) its close fell below the pause level (default **95%**) of its 12-month highest close, so it paused;
  2. its close is now at least the near-high level (95%) of its 12-month highest close again;
  3. RS ratio, EMA, closing range and monthly return pass the same tests as Setup A (points 3 to 5).
  After a second chance it needs another pause before the next one. This applies to every stock, whether it was missed, bought and sold, or never considered; a stock already held is not bought again. After the window, a stock can come back only through a new Setup A.
- **Slots and ranking.** Setup A and second-chance stocks compete for the free slots in one list, ranked as in 4.3 (closing range, then RS, then monthly return).
- **The 12-month high rolls.** Old prices drop out of the 12-month window, so a stock can come within 5% of its 12-month high because an older, higher close has dropped out, not because of a breakout. This is accepted (user's choice, 26 Nov 2026).
- Trades, holdings, the orders panel and the running month show each stock's signal (A or C for second chance), and "How the trades went" shows results for each.

**4.13 Market breadth rule (on by default).** Market breadth at a month-end = the share of the universe's stocks that have a close that month whose close is at or above their 12-month EMA (the same EMA as 4.4). The universe is the backtest's own: the chosen index or your CSV, after the lending and financial exclusions.
- **Sell everything:** at a month-end when breadth is **below the level (50% by default) and lower than at the month-end before**, every holding is sold at the next month's first open (reason "Market breadth below 50% and falling", unless a stock's own sell rule also fired) and nothing is bought. The money waits in cash and earns the cash yield.
- **Back to normal:** at any month-end when breadth is 50% or more, **or** higher than (or equal to) the month before, even below 50%, the normal buy and sell rules apply again. Stocks are then bought as usual (4.2, 4.3); the rule does not wait for 50%.
- Checked **once a month only**, at the month-end close, like every other rule. Breadth during the month is ignored.
- It works the same with the RS rule and with Setup A + second chance, and in the orders panel, the running month, Compare all indices and Test every start month. Each index in Compare uses its own breadth.
- Breadth swinging around 50% can make the rule sell and buy back in consecutive months; each round costs the trading cost on the whole portfolio. Buffers tested to reduce this (ignoring moves under 3 or 5 points, a 48%/52% band, two falling months in a row) did worse in testing (research note, 28 Nov 2026).
- The results show a **Market breadth** table (each decision month-end, breadth, change, decision), and the Excel file has a "Market breadth" sheet. Switch the rule off in the settings to see the strategy without it.

---

## 5. Current month and orders

**5.1 Orders for the next open.** When the backtest ends at the last completed month, the app shows the orders for the next month's first open, decided at that month-end close (final, not provisional). It first shows market breadth at that close and what the breadth rule (4.13) decides; when it says sell everything, "Start fresh" buys nothing and "Continue" sells every holding.
- **Start fresh** (a new portfolio): the top qualifying stocks (4.2 or 4.12, ranked by 4.3), one per slot. Quantity = whole shares of (starting capital ÷ slots) at the month-end close; if one share costs more than that, the quantity is 0 and the app says so. "Next in line" lists five more; the backtest itself never uses them.
- **Continue the backtest portfolio:** the backtest's own sells (with reasons), holdings to keep, and buys for the free slots, as portfolio weights.
- If the latest closing prices have not reached Yahoo yet, the panel warns; the next run fetches them again (at most every 20 minutes; when most stocks lack the day, 5 are checked first, see 4.1).

**5.2 The running month.** After the next month has started, the app shows that month's holdings (final: decided at the previous month-end) with their returns so far (provisional), what breadth would be and what the breadth rule would decide if the month closed at the latest prices, and what the rules would sell and buy (all provisional until the month ends).

---

## 6. Compare all indices and test every start month

**6.1 Compare all indices.** Runs the current backtest settings on all ten Nifty indices (the Nasdaq 100 is not included), for the chosen period and, if the chosen start is later, also from January 2015 to the same end. Each index is backtested on its own (ranking happens inside that index); the results equal single backtests of the same index. Each run is compared with the Nifty 50 and with the index's own value (its Total Return Index where available, otherwise its price index). Everything is written to one Excel workbook: summary, runs, settings, monthly values, trades and holdings.

**6.2 Test every start month.** Runs the current backtest settings once for every start month from the chosen start month to three months before the chosen end month, each run ending at the chosen end month. Each run starts fresh with all the money in cash, exactly like a single backtest with that start month (its numbers equal a single backtest's). It shows how much the result depends on when you start (see 9, "Start-date dependence"). Annual return (CAGR) is shown only for runs of 12 months or more; shorter runs show their total return. The deepest fall of each run is given with the month-end high it fell from, the low, and the month it was made good (or "Not yet"). The download is one zip file: a summary workbook (overview, one row per start month, calendar-year returns by start month, month-by-month values and drawdowns of every run) and each run's own workbook, the same file a single backtest gives.

---

## 7. How results are measured

- **CAGR:** the yearly growth rate from the first to the last month-end value.
- **Total return:** last value ÷ first value − 1.
- **Deepest fall (maximum drawdown):** the largest drop from a previous peak, using month-end values (falls within a month are not seen).
- **Volatility:** standard deviation of monthly returns × √12.
- **Sharpe ratio:** average monthly return above the cash yield ÷ standard deviation of monthly returns, × √12.
- **Calmar:** CAGR ÷ deepest fall.
- **Months ahead of the index:** share of months in which the strategy's return beat the benchmark's.
- **Win rate, average winner and loser, median trade, profit factor:** closed trades only; profit factor = total profit of winners ÷ total loss of losers.
- **Average money invested:** average share of the portfolio in stocks at month-ends.

---

## 8. Guidelines for live use

These are recommendations for following the strategy with real money. The code does not enforce them.

1. **Decide after the month has closed.** Run the backtest after 5 pm IST on the month's last trading day (after about 7 pm is safer, once Yahoo has the final prices). If the orders panel warns that prices are behind, wait and run again.
2. **Act only on final lists.** Do not trade on anything marked provisional.
3. **Start fresh, and keep the start month fixed.** A new portfolio should use "Start fresh". Afterwards, always run with the month you started as the start month, so the app's holdings match yours.
4. **Trade at the open.** Place orders in NSE's pre-open session (9:00 to 9:08 am) or right after 9:15 am on the first trading day of the month. Sell first, then buy.
5. **Decide the awkward cases in advance and apply them every month:** a stock locked at its upper circuit (buy the next day, or leave the slot in cash); a stock that cannot be sold at its lower circuit (sell the next trading day); a share price above the per-stock amount (buy one share, or leave the slot in cash); whether to use "Next in line" at all.
6. **No action during the month.** The rules have no stop-loss. Adding one makes it a different strategy; backtest it first.
7. **Keep a trade log** (date, stock, model price, your fill, quantity, charges) and compare it with the model every month.
8. **Size for liquidity.** Keep each order a small fraction of the stock's average daily traded value, especially for small and micro caps.
9. **Remember what the backtest leaves out:** taxes (short-term gains on listed shares are taxed at 20% plus cess for holdings up to 12 months), slippage beyond the cost setting, and survivorship bias (section 9).
10. **Start small.** Until the live results match the model for a few months, use part of the capital or paper-trade.
11. **Nasdaq 100.** Run the backtest after 8 pm New York time on the month's last US trading day (from about 6:30 am IST the next morning; 5:30 am during US summer time). The US market opens at 9:30 am New York time, which is 8 pm IST (7 pm during US summer time) on the first US trading day of the month. US market holidays differ from Indian ones. For an investor in India, buying US shares goes through the Liberalised Remittance Scheme, and foreign shares are taxed differently from Indian listed shares; check both before following the US results with real money. The default 6% cash yield is high for US dollars; set it to a realistic dollar rate.

---

## 9. Known limitations

- **Survivorship bias.** Today's index members are used for every past month. Companies that collapsed or left the index are missing, which makes results look better than they were, most of all for small and micro caps. It is also large for the **Nasdaq 100**: the index is rebuilt every December, and today's list holds the companies that grew big enough to join it, so a long backtest on today's members picks past winners with hindsight.
- **Start-date dependence.** The backtest never swaps a holding for a stronger stock and never trims winners, so what a run holds at any moment depends on what it bought earlier. Two runs with the same rules but different start months can therefore show very different returns for the same calendar year. Judge the strategy across many start months (6.2), not from one run.
- **Currency.** Nasdaq 100 results are in US dollars. The rupee's move against the dollar, remittance costs and taxes on foreign shares are not modelled.
- **Classification today.** The lending filter uses today's NSE classification for every past month.
- **Data quality.** Yahoo Finance data for small and micro caps can have gaps or bad prices; only one-day reversals are removed.
- **Month-end view.** Drawdowns and stops are only seen at month-ends; a stock can fall much further within a month.
- **Part-shares and fills.** The backtest assumes part-shares and that every order fills at the first open.
- **Screener and backtest differ** (see section 10).

---

## 10. Screener and backtest: differences

| | Screener | Backtest |
|---|---|---|
| Month used for the filters | The latest month, usually still running | Completed months only |
| RS rule | Marked if at a 12-month high or at/above the threshold, in any shown month | At a 12-month high, or at/above the buy level (the threshold by default) **and** higher than 2 month-ends earlier |
| Ranking | None (sorted for display) | Closing range (or chosen ranking), then RS ratio, then monthly return |
| History needed | 13 month-ends | 12 month-ends |
| Prices | 3 years of Yahoo monthly prices | Daily prices from 2010, cleaned, turned into monthly candles |
| Default RS threshold | 100% | 95% (buy level 95% too) |

Use the backtest's orders panel, not the screener, for the stocks to buy.

---

## 11. Market breadth and Sector rotation tabs

These two tabs came from the Abdullah Dashboard (github.com/Nhanna234/Abdullah_Dashboard) on 28 Nov 2026. The calculations are unchanged (`market_dash.py`, `sectors.py`, `rs_leaders.py`; tested to give identical output to the dashboard's own code). They are **daily** views of the market and do not feed the screener or the backtest. Their "market breadth" (% of Nifty 500 above the 50-day average) is a different, faster measure from the backtest's monthly breadth rule (4.13, % of the universe above the 12-month EMA).

**11.1 Data.** Index closes of the Nifty 50, Nifty Midcap 150 and Nifty Smallcap 250 from niftyindices.com (Yahoo Finance if that fails). Nifty 500 members from the NSE Indices list (the same list the screener uses). Daily stock closes from this app's stored Yahoo prices, adjusted for splits and dividends (the same prices the backtest uses; if they cannot be read, they are downloaded from Yahoo as the dashboard did). A day's prices count after 5 pm IST. Index closes are kept for an hour, stock prices for 6 hours and lists for a day; **Refresh data** clears them and reloads the page.

**11.2 Trend state of each index (Market breadth tab), every session.** Fast EMA = 10 days, slow EMA = 50 days, each started from the average of its first closes; 3-month low = the lowest close of the last 63 sessions.
- **S-UT (strong uptrend):** close above both EMAs and the 10-day EMA above the 50-day EMA.
- **UT (uptrend):** any other close above the 50-day EMA.
- **DT (downtrend):** not above the 50-day EMA and at a 3-month low. "At a 3-month low" depends on the chosen setting: a fresh 3-month closing low within the last 10 sessions (default), today's close being the low, or a close within 2% of the low.
- **S-DT (strong downtrend):** DT, plus close below both EMAs and the 10-day EMA below the 50-day EMA.
- **TZ (transition zone):** everything else.

**11.3 Regime, posture and score.** Score: S-UT +2, UT +1, TZ 0, DT −1, S-DT −2, added over the three indices (−6 to +6); a change of 3 or more over 5 sessions is called improving or deteriorating.

| Indices in uptrend (UT/S-UT) or downtrend (DT/S-DT) | Regime | Posture |
|---|---|---|
| All three up (all S-UT: "strong") | Broad (strong) uptrend | Risk-on |
| All three down (all S-DT: "strong") | Broad (strong) downtrend | Risk-off |
| At least one up and one down | Divergent | Cautious |
| Only the Nifty 50 up | Narrow: large-caps only | Cautious |
| Nifty 50 and one other up | Breadth narrowing | Selective |
| Midcap and Smallcap up, Nifty 50 not | Broader market leading | Constructive |
| One of Midcap/Smallcap up, nothing down | Mixed | Selective |
| None up, none down | Correction / no trend | Defensive |
| None up, some down | Weakening | Defensive |

**11.4 Breadth on this tab.** Participation = share of Nifty 500 stocks whose close is above their own 50-day simple average (stocks with 50 days of prices). Zones: 80%+ Overheated, 60%+ Healthy, 40%+ Neutral, 20%+ Weak, below 20% Washed-out. Against the posture: in Risk-on or Constructive it **confirms** at 50%+ and **diverges** below 40%; in Defensive or Risk-off it confirms at 40% or less and diverges at 50%+; otherwise neutral. A change of 10 points or more over 5 sessions is flagged; a **breadth thrust** is a move from below 25% to above 60% within 10 sessions. The longer-term line counts Nifty 500 stocks whose 50-day EMA is above their 200-day EMA. Each session's written remark is built from these facts.

**11.5 Sector rotation tab.** NSE sectoral and thematic indices plus the Nifty 50/200/500 ("Major" by default, or all NSE indices). Returns use the first close on or after the date N calendar days back: 1W 7, 1M 30, 3M 90 (or 60, as in the original Google Sheet), 6M 180, 1Y 365 days. **Rank(1,3,6)** = 0.5 × rank of the 1M return + 0.3 × rank of the 3M return + 0.2 × rank of the 6M return (rank 1 = best; lowest score first, ties by the better 1M return). It can also be ranked by 3M return or by distance from the 52-week low. The rank history shows each index's position at every week's last close, the leaders are the top 5, and the notes describe moves over 4 weeks.

**11.6 Strong stocks inside the leading indices.** The members of the ticked indices are screened with the screener's RS rules on monthly Yahoo prices (3 years): RS 100% by default, last 6 months shown, close above the 12-month EMA, closing range 75%+, monthly return 5%+. Lending companies are left out by default with the same filter as 1.4. The running month is provisional until it ends.

---

## 12. Parameters

The values the code uses. This section is generated from the code; the website always shows the current values.

<!-- parameters:start (generated by rules_doc.py from the code; do not edit by hand) -->
| Section | Parameter | Value | Defined in |
|---|---|---|---|
| Universes | Default universe (both tabs) | Nifty Total Market | settings.py `DEFAULT_INDEX` |
| Universes | Indices offered | Nifty 50, Nifty 100, Nifty 200, Nifty LargeMidcap 250, Nifty Midcap 150, Nifty Smallcap 100, Nifty Smallcap 250, Nifty Microcap 250, Nifty 500, Nifty Total Market, Nasdaq 100 | settings.py `INDEX_FILES`, `US_INDICES` |
| Universes | Nasdaq 100 list | Nasdaq's website, then Wikipedia, then the saved snapshot of 20 Nov 2026 | us_universe.py `SNAPSHOT_DATE` |
| Universes | Lending companies excluded by default | On | settings.py `SCREENER_DEFAULTS`, `BT_DEFAULTS` |
| RS measure | Benchmark for relative strength | Nifty 50 price index (Yahoo `^NSEI`) | settings.py `RS_INDEX_TICKER` |
| RS measure | Benchmark for relative strength, Nasdaq 100 and US lists | S&P 500 price index (Yahoo `^GSPC`) | settings.py `MARKETS` |
| RS measure | Look-back for the RS high (screener / backtest) | 12 / 12 month-ends | settings.py `LOOKBACK_MONTHS`, bt_engine.py `LOOKBACK` |
| RS measure | EMA length | 12 months | bt_engine.py `EMA_SPAN` |
| Screener | Months shown | 6 | settings.py `SCREENER_DEFAULTS` |
| Screener | RS threshold | 100% | settings.py `SCREENER_DEFAULTS` |
| Screener | Price above 12-month EMA | On | settings.py `SCREENER_DEFAULTS` |
| Screener | Minimum closing range | 75% | settings.py `SCREENER_DEFAULTS` |
| Screener | Minimum monthly return | 5% | settings.py `SCREENER_DEFAULTS` |
| Screener | Weekly drill-down | Off | settings.py `SCREENER_DEFAULTS` |
| Screener | Weekly drill-down: latest week's return above | 4% | settings.py `WEEKLY_RET_REQ` |
| Screener | Weekly drill-down: latest week's closing range above | 75% | settings.py `WEEKLY_CR_REQ` |
| Screener | Price history downloaded | 3y of monthly prices | settings.py `SCREENER_PRICE_PERIOD` |
| Screener | RS line drawn per stock | 24 months | settings.py `RS_TRAIL_MONTHS` |
| Backtest | Default start month | 2015-01 | settings.py `BT_DEFAULTS` |
| Backtest | Default end month | the last completed month | bt_data.py `last_completed_month` |
| Backtest | Earliest start month | 2012-01 | bt_data.py `EARLIEST_START` |
| Backtest | Stocks held (slots) | 10 | settings.py `BT_DEFAULTS` |
| Backtest | RS threshold (sell below it) | 95% | settings.py `BT_DEFAULTS` |
| Backtest | RS rule: buy when RS is at least | same as the RS threshold | settings.py `BT_DEFAULTS` `buyThreshold` |
| Backtest | Market breadth rule (sell everything, buy nothing) | on: when breadth is below 50% and lower than a month earlier; breadth = share of the universe's stocks closing at or above their 12-month EMA | settings.py `BT_DEFAULTS` `breadthRule`, `breadthLevel` |
| Backtest | Price above 12-month EMA (entry and exit) | On | settings.py `BT_DEFAULTS` |
| Backtest | Minimum closing range | 75% | settings.py `BT_DEFAULTS` |
| Backtest | Minimum monthly return | 5% | settings.py `BT_DEFAULTS` |
| Backtest | Ranking of candidates | Closing range (choices: Closing range, 6-month return, 12-month return) | settings.py `BT_DEFAULTS`, bt_engine.py `RANKINGS` |
| Backtest | Entry rule (default) | RS rule (choices: RS rule; Setup A: first close near the 12-month high, with a second chance after a pause) | settings.py `BT_DEFAULTS`, bt_engine.py `ENTRY_RULES` |
| Setup A | Setup A: close at least this share of the highest close | 95% | settings.py `BT_DEFAULTS` |
| Setup A | Setup A: highest close measured over | 12 months | bt_engine.py `HIGH_WINDOW` |
| Setup A | Setup A: first time in | 12 months | settings.py `BT_DEFAULTS` |
| Setup A | Second chance switched on | On | settings.py `BT_DEFAULTS` |
| Setup A | Second chance: after a month-end close below this share of the highest close | 95% | settings.py `BT_DEFAULTS` |
| Setup A | Second chance: within this many months of Setup A | 12 months | settings.py `BT_DEFAULTS` |
| Backtest | Cost per buy and per sell | 0.2% | settings.py `BT_DEFAULTS` |
| Backtest | Yield on cash | 6% a year | settings.py `BT_DEFAULTS` |
| Backtest | Starting capital | ₹1,000,000 | settings.py `BT_DEFAULTS` |
| Backtest | Starting capital, Nasdaq 100 and US lists | $10,000 | settings.py `MARKETS` |
| Backtest | Benchmark, Nasdaq 100 and US lists | S&P 500 Total Return Index (Yahoo `^SP500TR`), else the S&P 500 price index (`^GSPC`) | bt_data.py `US_BENCH_TRI`, `US_BENCH_PRICE` |
| Backtest | Smallest buy allowed | 20% of one slot's money | bt_engine.py `MIN_ALLOC_FRAC` |
| Data | Daily prices kept from | 2010-01-01 | bt_data.py `DATA_START` |
| Data | Bad-price rule | a one-day move of 1.6× (or 1/1.6) that fully reverses the next day | bt_engine.py `SPIKE_FACTOR` |
| Data | A day's prices count as final from | 17:00 IST | bt_data.py `SESSION_CUTOFF_IST` |
| Data | A day's prices count as final from, US stocks | 20:00 New York time | bt_data.py `SESSION_CUTOFF_US` |
| Data | Missing latest close asked again after | 20 minutes | bt_data.py `REFETCH_MINUTES` |
| Data | When most stocks lack the latest close (holiday or late data), stocks asked first | 5, once at least 50% of recently fetched stocks lack it | bt_data.py `LATE_PROBE`, `LATE_SHARE` |
| Data | Saved price file | GitHub release `price-store` of Nhanna234/RS-on-Nifty: `prices_in.tar.gz`, `prices_us.tar.gz` | price_snapshot.py `SNAPSHOT_REPO`, `SNAPSHOT_TAG`, `SNAPSHOT_FILES` |
| Data | Saved price file refreshed | weekdays at 20:00 IST and 08:00 IST (GitHub Actions) | `.github/workflows/price-snapshot.yml` |
| Data | Saved price file published only if stocks with prices are at least | 90% | price_snapshot.py `MIN_SHARE_WITH_DATA` |
| Lending filter | Basic industries treated as lending | Public Sector Bank, Private Sector Bank, Other Bank, Non Banking Financial Company (NBFC), Housing Finance Company, Financial Institution | lender_filter.py `LENDING_LABELS` |
| Lending filter | Fallback list of financial institutions | HUDCO, IFCI, IREDA, IRFC, PFC, RECLTD | lender_filter.py `KNOWN_FIS` |
| Lending filter | NSE classification kept for | 30 days | lender_filter.py `CACHE_DAYS` |
| Lending filter | US stocks: industry words treated as lending | bank(s), banking, consumer lending, consumer finance, mortgage, thrift, savings institution, specialty finance, commercial finance, credit services | lender_filter.py `US_LENDING_WORDS` |
| Comparison | Universes compared | Nifty Total Market, Nifty 500, Nifty 50, Nifty 100, Nifty 200, Nifty LargeMidcap 250, Nifty Midcap 150, Nifty Smallcap 100, Nifty Smallcap 250, Nifty Microcap 250 | settings.py `COMPARE_ORDER` |
| Comparison | Long period starts | 2015-01 | settings.py `LONG_START` |
| Start-month test | Shortest run | 3 months | sweep.py `SWEEP_MIN_MONTHS` |
| Start-month test | Annual return shown for runs of at least | 12 months | sweep.py `CAGR_MIN_MONTHS` |
| Start-month test | Most start months in one test | 240 | sweep.py `MAX_SWEEP_RUNS` |
| Market breadth tab | Trend EMAs (fast / slow) | 10 / 50 days | market_dash.py `CFG` |
| Market breadth tab | 3-month low | lowest close of 63 sessions; fresh low within 10 sessions (default), or within 2% | market_dash.py `CFG` |
| Market breadth tab | Participation | % of Nifty 500 above their 50-day average; 50-day EMA above 200-day EMA count | market_dash.py `CFG` |
| Market breadth tab | Breadth zones | 80%+ Overheated, 60%+ Healthy, 40%+ Neutral, 20%+ Weak, 0%+ Washed-out | market_dash.py `CFG` |
| Market breadth tab | Confirms / diverges | up postures: 50%+ / below 40%; down postures: 40% or less / 50%+ | market_dash.py `CFG` |
| Market breadth tab | Breadth thrust | below 25% to above 60% within 10 sessions | market_dash.py `CFG` |
| Market breadth tab | Score and breadth change flagged | score 3+ in 5 sessions; breadth 10+ points in 5 sessions | market_dash.py `CFG` |
| Sector rotation tab | Rank(1,3,6) weights | 1M 0.5, 3M 0.3, 6M 0.2 | sectors.py `SECTOR_CFG` |
| Sector rotation tab | Return look-backs (calendar days) | 1w 7, 1m 30, 6m 180, 1y 365, 3M 90 (or 60 as in the sheet) | sectors.py `SECTOR_CFG` |
| Sector rotation tab | Leaders / moves measured over | top 5 / 4 weeks; history up to 26 weeks | sectors.py `SECTOR_CFG` |
| Sector rotation tab | Stock screen defaults | RS 100%, 6 months shown, closing range 75%+, monthly return 5%+, EMA on | rs_leaders.py `RS_DEFAULTS` |
<!-- parameters:end -->

---

## 13. Change log

**28 Nov 2026 (4)**
- The Abdullah Dashboard is now part of this website: two new tabs, **Market breadth** (daily trend state of the Nifty 50, Midcap 150 and Smallcap 250, regime, Nifty 500 participation) and **Sector rotation** (Rank(1,3,6) of the sectoral and thematic indices, with the RS screen of their strongest stocks). Same calculations as the dashboard (section 11); the breadth page now reads the stock prices this app already stores instead of downloading 500 histories. The separate dashboard site is unchanged.

**28 Nov 2026 (3)**
- New backtest defaults, so the agreed setup runs without typing anything: RS threshold 95% (sell below 95%; the buy level follows it, so buy at 95% too), market breadth rule on at 50%, and lending companies plus all other financial companies excluded. Change any of them in the settings before running. The Screener keeps its own defaults.

**28 Nov 2026 (2)**
- New **market breadth rule**, on by default (4.13): at a month-end when fewer than 50% of the universe's stocks close at or above their 12-month EMA **and** that share is lower than a month earlier, sell everything at the next open and buy nothing; back to the normal rules as soon as breadth is 50% or more, or rising. In testing (Nifty Total Market, 2016–2026 starts) it cut the usual worst fall from about −27% to between −12% and −17%, with about the same returns. The orders panel and the running month show breadth and the decision; results and Excel show a breadth table. Switch it off, or change the 50% level, in the backtest settings.

**28 Nov 2026**
- Backtest, RS rule: new setting **Buy at RS % or more** (4.2). A stock is bought only when its RS reaches this level; selling still happens when RS falls below the RS threshold, as before. It starts equal to the RS threshold, so results are unchanged unless you raise it (for example to 100, to buy only at a new 12-month RS high). It also applies to the orders panel, the comparison and the start-month test.

**27 Nov 2026**
- Faster start after the website has been asleep: a copy of all daily prices is now saved on GitHub twice every weekday (20:00 and 08:00 IST) by a scheduled job, and the website loads that copy when it starts instead of downloading 15 years of prices for every stock again (4.1). If the copy cannot be loaded, prices come from Yahoo as before. No change to any rule or result.
- Holidays and late Yahoo data no longer make the app download every stock again every 20 minutes: when most stocks lack the latest day, 5 are checked first (4.1).

**26 Nov 2026**
- Replaced Setup B (pullback to the 12-month EMA, with 7 of 10 slots reserved for Setup A) with a **second chance** (4.12): a stock that had Setup A in the last 12 months, then closed more than 5% below its 12-month high, can be bought when it closes near its high again with the same RS and filters. Setup A and second-chance stocks share all slots and one ranking. The RS rule stays the default.
- Setup B now also needs the monthly return to be at least the minimum (default 5%), like Setup A.
- The website no longer asks for a username and password (sign-in switched off). No change to any rule.
- Backtest: new entry rule **Setup A + B** (4.12): buy a stock the first time in 12 months it closes within 5% of its 12-month highest close with RS of at least the threshold (Setup A), or later when it pulls back to its 12-month EMA and bounces while on the signals list (Setup B). Setup A holds at most 7 of 10 stocks; Setup B fills the rest. The RS rule stays the default; nothing changes unless you choose the new rule.

**22 Nov 2026**
- Added "Test every start month" in the Backtest tab (6.2): the same settings run from every start month to one end month, with a summary and every run's Excel file in one zip.
- Added the tick box "and all other financial companies" to the lending filter (1.4), to leave out the whole Financial Services sector.
- Each backtest workbook's settings now also list whether other financial companies and your always-exclude stocks were left out. No change to the screener or backtest rules.

**21 Nov 2026 (evening)**
- The website now asks for a username and password. Accounts are kept in the app's Secrets in Streamlit Cloud (no database); passwords are stored only in scrambled form. No change to any screener or backtest rule.

**21 Nov 2026**
- Added the **Nasdaq 100** universe (section 1.5). For it, relative strength is measured against the S&P 500 instead of the Nifty 50, the benchmark is the S&P 500 Total Return Index, money is in US dollars ($10,000 default capital), and a day counts as final from 8 pm New York time. The member list comes from Nasdaq, with Wikipedia and a saved snapshot as fallbacks.
- An uploaded CSV is now read as stocks of the market of the selected index (NSE stocks with a Nifty index, US stocks with the Nasdaq 100).
- The lending filter has a US version based on the stock list's industry (1.4). "Compare all indices" still covers the ten Nifty indices only.
- No change to the screener or backtest rules for the Nifty indices.

**19 Nov 2026**
- Added this document and the Rules tab; all adjustable values moved to `settings.py`.
- Added "Compare all indices": the same settings on all ten indices, for the chosen period and since January 2015, with each index's own value as a second benchmark.

**18 Nov 2026**
- Added orders for the next open ("Start fresh" and "Continue the backtest portfolio").
- A month now counts as complete from 5 pm IST on its last weekday (before: on its last calendar day).
- A stock missing the latest close is fetched again on the next run (at most every 20 minutes), with a warning in the orders panel.
- Added the lending-companies filter, on by default in both tabs.
- Added the running month view: final holdings, provisional returns and provisional changes.

**17 Nov 2026**
- Added the backtest (rules in section 4), agreed as: empty slots in cash; sell only on RS below threshold or close below the 12-month EMA; buy below 100% only if the RS ratio is higher than 2 month-ends earlier; no swapping; no rebalancing; trades at the next month's first open.
- Screener fix: the minimum closing range and monthly return had been divided by 100 before comparison, so 75% and 5% acted as 0.75% and 0.05%. They now apply as entered.

**16 Nov 2026**
- Added the ten Nifty index universes (default Nifty Total Market) and the CSV upload that replaces the index.
- Default RS threshold of the screener changed from 95% to 100%.
- Fix: when Yahoo returned two rows for one month, the screener failed; the rows are now merged.
- New dashboard interface.
