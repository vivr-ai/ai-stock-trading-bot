import Term from "@/components/Term";
import ExampleCard from "@/components/ExampleCard";
import StrategyFlowDiagram from "@/components/StrategyFlowDiagram";
import { ShieldCheck, Layers, Ban } from "lucide-react";

export const metadata = {
  title: "Trading Strategy",
};

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-bg-border bg-bg-panel p-5">
      <h2 className="mb-3 text-base font-semibold text-white">{title}</h2>
      <div className="space-y-3 text-sm leading-relaxed text-muted">{children}</div>
    </section>
  );
}

export default function StrategyPage() {
  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-semibold text-white">Trading Strategy</h1>
        <p className="text-sm text-muted">
          A plain-English explanation of what this bot does and why — no investing background required.
        </p>
      </div>

      <Section title="The objective">
        <p>
          This bot trades a fixed watch-list of stocks using{" "}
          <Term definition="A simulated brokerage account. Orders are real in every way except no real money changes hands, which makes it safe for testing a strategy.">
            paper trading
          </Term>{" "}
          on Alpaca — meaning every trade you see is simulated, not real money. Its only input is recent news:
          it reads headlines about a stock, judges whether the news is good or bad, and decides whether to buy,
          sell, or do nothing. It runs on a fixed schedule, follows the same rules every time, and never trades
          on emotion, hunches, or headlines it hasn&apos;t actually read.
        </p>
        <p>
          The goal isn&apos;t to predict the market perfectly. It&apos;s to react faster and more consistently
          than a human could to news that moves a stock&apos;s price, while a set of hard risk limits keeps any
          single bad call from doing much damage.
        </p>
      </Section>

      <Section title="How a decision gets made, step by step">
        <p>Every cycle (roughly every 30 minutes while the US market is open) the bot repeats the same routine:</p>
        <ol className="list-decimal space-y-2 pl-5">
          <li>
            <span className="text-white">Check overall market health.</span> It looks at a broad market index
            (SPY). If the market itself is down sharply today, or trading below its own long-term{" "}
            <Term definition="The average closing price over a recent period (e.g. the last 50 days). Used to judge whether a stock or the market is in a longer-term uptrend or downtrend.">
              moving average
            </Term>
            , the bot pauses new buys — it will still watch and sell existing positions, but won&apos;t open new
            ones into a falling market.
          </li>
          <li>
            <span className="text-white">Read the news.</span> For each stock on the watch-list, it pulls recent
            headlines. If there aren&apos;t at least a handful of headlines to go on (5 to consider buying, 3 to
            consider selling), it treats the evidence as too thin and holds off.
          </li>
          <li>
            <span className="text-white">Score the sentiment.</span> The headlines are scored on a scale from
            -10 (very negative) to +10 (very positive) — this is the bot&apos;s{" "}
            <Term definition="A number from -10 to +10 summarizing how positive or negative the recent news coverage is for a stock. Positive scores lean toward buying, negative scores lean toward selling.">
              sentiment score
            </Term>
            .
          </li>
          <li>
            <span className="text-white">Confirm with price and volume.</span> A good news score alone isn&apos;t
            enough. The bot also checks the stock is trading above its own 20-day moving average (confirming the
            trend agrees) and that today&apos;s trading{" "}
            <Term definition="Today's trading volume compared to the recent 20-day average. A ratio above 1.0 means more shares are trading hands than usual - a sign the market has genuinely noticed the news.">
              volume ratio
            </Term>{" "}
            is at least 1.5x the recent average, meaning real trading interest backs up the news, not just a
            quiet, thinly-traded blip. It also avoids chasing a stock that has already jumped more than 8% today.
          </li>
          <li>
            <span className="text-white">Apply the risk rules.</span> Even a strong buy signal can be blocked —
            by position size limits, sector limits, available cash, or a cooldown on a stock recently traded (see
            below).
          </li>
          <li>
            <span className="text-white">Act.</span> If everything lines up, the bot places a real (paper) order.
            A buy automatically comes with a stop-loss and take-profit already attached. Otherwise it holds.
          </li>
          <li>
            <span className="text-white">Wait, then repeat.</span> The whole routine runs again next cycle,
            independently — nothing carries over except its actual open positions.
          </li>
        </ol>
        <div className="pt-2">
          <StrategyFlowDiagram />
        </div>
      </Section>

      <Section title="Risk management rules">
        <p>These limits exist to make sure no single stock, sector, or bad day can do outsized damage:</p>
        <ul className="list-disc space-y-2 pl-5">
          <li>
            <span className="text-white">Stop-loss at -10%.</span> Every position automatically sells if it
            falls 10% below its entry price — capping the loss on any single trade.
          </li>
          <li>
            <span className="text-white">Take-profit at +20%.</span> Every position automatically sells if it
            gains 20%, locking in the win rather than hoping for more.
          </li>
          <li>
            <span className="text-white">Sentiment-driven exit at -5.</span> Independent of price, if the news
            sentiment on a stock the bot holds turns sharply negative (score of -5 or worse), it can sell early —
            it doesn&apos;t need to wait for the stop-loss to be hit.
          </li>
          <li>
            <span className="text-white">Daily loss limit of 4%.</span> If the whole portfolio drops 4% in a
            single day, the bot stops opening new positions for the rest of that day.
          </li>
          <li>
            <span className="text-white">
              24-hour{" "}
              <Term definition="A waiting period after selling a stock before the bot will consider buying it again, to avoid rapidly flip-flopping in and out of the same position on noisy, back-and-forth news.">
                cooldown
              </Term>{" "}
              on re-entry.
            </span>{" "}
            After selling a stock, the bot won&apos;t buy it back for 24 hours, avoiding rapid flip-flopping.
          </li>
          <li>
            <span className="text-white">Market regime filter.</span> New buys pause when the broader market
            (SPY) is down more than 2% on the day or below its 50-day average — described in step 1 above.
          </li>
        </ul>
      </Section>

      <Section title="Position sizing and exposure limits">
        <p>How much the bot puts into any one trade, and how much of the portfolio it will risk overall:</p>
        <ul className="list-disc space-y-2 pl-5">
          <li>
            <span className="text-white">Max 5% of the portfolio per position.</span> No single stock can grow
            to dominate the account.
          </li>
          <li>
            <span className="text-white">
              Max 50% total{" "}
              <Term definition="The share of the portfolio currently invested in stocks, as opposed to sitting in cash. Lower exposure means less money is at risk if the market drops.">
                exposure
              </Term>
              .
            </span>{" "}
            At least half the portfolio stays in cash at all times, as a buffer.
          </li>
          <li>
            <span className="text-white">Max 10 open positions</span> at once, and{" "}
            <span className="text-white">no more than 3 new positions per cycle</span> — so the bot can&apos;t
            suddenly deploy the whole portfolio in one 30-minute window.
          </li>
          <li>
            <span className="text-white">
              Max 3 positions per{" "}
              <Term definition="A group of companies in the same industry (e.g. technology, healthcare, energy). Limiting how many positions can sit in one sector avoids the portfolio being quietly concentrated in a single industry's fortunes.">
                sector
              </Term>
              .
            </span>{" "}
            Keeps the portfolio from being quietly concentrated in one industry even if several stocks in it look
            attractive at once.
          </li>
        </ul>
        <div className="mt-3 grid gap-3 sm:grid-cols-3">
          <div className="flex items-center gap-2 rounded-lg border border-bg-border bg-bg-panel2 p-3 text-xs text-muted">
            <Layers size={16} className="shrink-0 text-accent" /> 5% per position, 50% max invested
          </div>
          <div className="flex items-center gap-2 rounded-lg border border-bg-border bg-bg-panel2 p-3 text-xs text-muted">
            <ShieldCheck size={16} className="shrink-0 text-gain" /> Stop-loss -10% / take-profit +20% on every trade
          </div>
          <div className="flex items-center gap-2 rounded-lg border border-bg-border bg-bg-panel2 p-3 text-xs text-muted">
            <Ban size={16} className="shrink-0 text-loss" /> Auto-pause after a 4% daily loss
          </div>
        </div>
      </Section>

      <Section title="Worked examples">
        <div className="grid gap-3 md:grid-cols-3">
          <ExampleCard type="buy" title="Positive earnings coverage">
            A stock has 9 fresh headlines about a strong earnings beat. Sentiment scores +8.2. It&apos;s trading
            above its 20-day average with 1.8x normal volume. Cash is available, the sector isn&apos;t already at
            its limit, and the market overall is calm. The bot buys, and a -10% stop-loss and +20% take-profit
            are attached immediately.
          </ExampleCard>
          <ExampleCard type="sell" title="Sentiment sours on a held position">
            The bot holds a stock bought two weeks ago. New headlines about a product recall push sentiment to
            -6.5. The price hasn&apos;t yet dropped 10%, but because the sentiment rule (-5 threshold) is
            triggered independently, the bot sells early rather than waiting for the stop-loss.
          </ExampleCard>
          <ExampleCard type="hold" title="Not enough to go on">
            A stock has only 2 headlines today, and they&apos;re mildly positive. That&apos;s below the minimum
            of 5 headlines the bot requires before acting on a buy signal. Evidence is too thin, so the bot holds
            and waits for the next cycle.
          </ExampleCard>
        </div>
      </Section>
    </div>
  );
}
