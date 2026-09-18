import Link from "next/link";
import Term from "@/components/Term";
import ExampleCard from "@/components/ExampleCard";
import StrategyFlowDiagram from "@/components/StrategyFlowDiagram";
import { ShieldCheck, Layers, Ban, FlaskConical, ArrowRight } from "lucide-react";

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
          on Alpaca — meaning every trade you see is simulated, not real money. It runs on a fixed schedule,
          follows the same rules every time, and never trades on emotion, hunches, or headlines it hasn&apos;t
          actually read.
        </p>
        <p>
          It looks for two different, independent kinds of opportunity, described in detail below:
        </p>
        <ul className="list-disc space-y-2 pl-5">
          <li>
            <span className="text-white">Path A — news-driven momentum.</span> React to genuinely good or bad
            news faster and more consistently than a human could. This is the bot&apos;s original strategy, and
            the only one currently allowed to place real orders.
          </li>
          <li>
            <span className="text-white">Path B — technical mean-reversion.</span> A newer, second signal that
            looks for healthy stocks that have dipped sharply in the very short term, independent of the news.
            It&apos;s currently running in <span className="text-white">shadow mode</span> — see below.
          </li>
        </ul>
        <p>
          The goal isn&apos;t to predict the market perfectly. It&apos;s to react faster and more consistently
          than a human could to genuine signals, while a set of hard risk limits keeps any single bad call from
          doing much damage.
        </p>
      </Section>

      <Section title="How a decision gets made, step by step">
        <p>
          Every cycle (roughly every 30 minutes while the US market is open), for every stock on the watch-list,
          the bot runs both paths below and combines the result with its risk rules.
        </p>
        <div className="pt-1">
          <StrategyFlowDiagram />
        </div>
      </Section>

      <Section title="Path A — news-driven momentum">
        <ol className="list-decimal space-y-2 pl-5">
          <li>
            <span className="text-white">Check overall market health.</span> It looks at a broad market index
            (SPY). If the market itself is down sharply today, or trading below its own long-term{" "}
            <Term definition="The average closing price over a recent period (e.g. the last 50 days). Used to judge whether a stock or the market is in a longer-term uptrend or downtrend.">
              moving average
            </Term>
            , the bot pauses new buys on both paths — it will still watch and sell existing positions, but
            won&apos;t open new ones into a falling market.
          </li>
          <li>
            <span className="text-white">Read the news, score the sentiment.</span> For each stock, it pulls
            recent headlines and scores them from -10 (very negative) to +10 (very positive) — the bot&apos;s{" "}
            <Term definition="A number from -10 to +10 summarizing how positive or negative the recent news coverage is for a stock. Positive scores lean toward buying, negative scores lean toward selling.">
              sentiment score
            </Term>
            .
          </li>
          <li>
            <span className="text-white">Combine three signals into one weighted score</span> — this is the
            main change from the bot&apos;s original rules. Instead of requiring every box to be ticked before
            it would even consider buying, three things each earn partial credit toward one composite score out
            of 1.0:
            <ul className="mt-2 list-disc space-y-1.5 pl-5">
              <li><span className="text-white">Sentiment quality (50% of the score).</span> How strongly positive the news is.</li>
              <li>
                <span className="text-white">Headline coverage (20% of the score).</span> How many articles back
                it up, up to 5 headlines for full credit.
              </li>
              <li>
                <span className="text-white">Volume confirmation (30% of the score).</span> Today&apos;s{" "}
                <Term definition="Today's trading volume compared to how much volume is normally expected by this point in the trading session, based on the recent 20-day average. Above 1.0x means more shares are trading hands than usual for this time of day - a sign the market has genuinely noticed the news.">
                  volume ratio
                </Term>
                , up to 1.5x normal for full credit.
              </li>
            </ul>
            A stock needs a combined score of at least 0.60 to buy — so a strong, well-covered story with only
            so-so volume can still clear the bar, where the old all-or-nothing rule would have blocked it on
            that one weak leg alone.
          </li>
          <li>
            <span className="text-white">Two checks still can&apos;t be bought around.</span> No matter how high
            the score, the bot won&apos;t buy a stock that&apos;s already up more than 8% today (the good news is
            probably priced in already), or one trading below its own 20-day moving average (the price hasn&apos;t
            confirmed the story yet).
          </li>
          <li>
            <span className="text-white">Apply the risk rules, then act.</span> Even a strong buy signal can
            still be blocked — by position size limits, sector limits, available cash, or a cooldown on a stock
            recently traded (see below). If everything lines up, the bot places a real (paper) order with a
            stop-loss and take-profit attached immediately.
          </li>
        </ol>
      </Section>

      <Section title="Path B — technical mean-reversion (RSI-2)">
        <div className="mb-3 flex items-start gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-amber-200">
          <FlaskConical size={16} className="mt-0.5 shrink-0 text-amber-400" />
          <p className="text-xs leading-relaxed">
            <span className="font-semibold text-amber-400">Currently in shadow mode.</span> The bot evaluates
            this signal every cycle and logs exactly what it would have bought and why, but it does not place
            any order. No paper money is on the line for Path B yet — it&apos;s being observed first. Flipping
            it on to trade for real is a deliberate, separate configuration change.
          </p>
        </div>
        <p>
          This is a different idea from Path A: rather than reacting to news, it looks for a stock that&apos;s
          genuinely healthy on a long time horizon but has dropped sharply in the very short term — a classic
          oversold bounce setup (the style popularised by trader Larry Connors&apos; RSI-2 system). All of the
          following have to be true at once:
        </p>
        <ul className="list-disc space-y-2 pl-5">
          <li>
            <span className="text-white">Long-term uptrend confirmed.</span> The price is above its own 200-day
            moving average — this filters out stocks that are simply in genuine decline.
          </li>
          <li>
            <span className="text-white">Sharply, briefly oversold.</span> Its{" "}
            <Term definition="Relative Strength Index over a 2-day window: a 0-100 reading of how fast and how far a stock has fallen in the very short term. Below 10 is an extreme, short-lived oversold reading; above 65 suggests the bounce has largely played out.">
              2-day RSI
            </Term>{" "}
            reads 10 or below.
          </li>
          <li>
            <span className="text-white">Volume-confirmed.</span> Today&apos;s trading volume is at least 1.3x
            the normal amount expected by this point in the session — a sign of real capitulation buying, not a
            quiet drift lower on thin volume.
          </li>
          <li>
            <span className="text-white">Not vetoed by bad news.</span> If the sentiment score is -5 or worse,
            the dip is skipped — that&apos;s more likely a real bad-news story than a technical bounce worth
            buying.
          </li>
        </ul>
        <p>
          If it ever goes live, a Path B position would exit on its own rule too — once RSI climbs back above
          65 (the bounce has largely played out) or after 5 days if it hasn&apos;t reverted by then — rather than
          the sentiment-exit rule below, though the same stop-loss and take-profit safety net would still apply
          underneath it.
        </p>
        <Link
          href="/shadow-comparison"
          className="inline-flex items-center gap-1.5 text-sm font-medium text-amber-400 hover:text-amber-300"
        >
          See how Path B&apos;s shadow signals compare to what actually traded <ArrowRight size={14} />
        </Link>
      </Section>

      <Section title="Risk management rules">
        <p>These limits exist to make sure no single stock, sector, or bad day can do outsized damage — they apply to every position, regardless of which path opened it:</p>
        <ul className="list-disc space-y-2 pl-5">
          <li>
            <span className="text-white">Stop-loss at -10%.</span> Every position automatically sells if it
            falls 10% below its entry price — capping the loss on any single trade.
          </li>
          <li>
            <span className="text-white">Take-profit: a fixed +20% flag, or a trailing stop that lets it run —
            never both.</span> This is a single switch for the whole bot, not a per-trade choice, and it&apos;s
            decided once, the moment a position is bought, by whichever mode the bot is in at that instant:
            <ul className="mt-2 list-disc space-y-1.5 pl-5">
              <li>
                <span className="text-white">Switch off (the plain default).</span> Every new position gets one
                simple order taped to it: sell the instant it&apos;s up 20%. Fixed target, no exceptions — the
                trailing logic below never even runs.
              </li>
              <li>
                <span className="text-white">Switch on ({" "}
                <Term definition="A protective exit that follows a position's price upward instead of selling at one fixed target, so a strong winner isn't capped at the same level as a modest one. A config setting turns it on for the whole bot at once — it isn't decided trade by trade.">
                  trailing stop
                </Term>
                {" "}enabled — this bot&apos;s current setting).</span> The 20% flag isn&apos;t placed at all.
                Instead, every new position gets a much further-out 50% backstop, and the bot starts tracking
                its{" "}
                <Term definition="The highest price a position has reached since it was bought, tracked continuously while the position is held. Used only by the trailing-stop layer to judge how far the price has since pulled back.">
                  peak
                </Term>{" "}
                — the highest price it has reached so far. Once the position is up at least 3% from where it
                was bought, the trailing stop arms itself and starts watching for a pullback. From then on, the
                moment the price falls 7% below its peak, the bot sells right there — banking whatever gain
                that locks in, which is usually well past 20%, but could occasionally be less if the position
                barely got going before turning back down. If the price somehow keeps climbing without ever
                pulling back 7%, the 50% backstop alone would eventually end the trade — but in practice the
                7% pullback almost always fires first.
              </li>
            </ul>
            Because the mode is set once per trade and never changes mid-flight, a single position never has
            both the 20% flag and a trailing check racing each other — one or the other, decided at entry. The
            one exception: a position bought <em>before</em> this setting was switched on keeps whichever order
            it already had (possibly the old 20% flag), and the trailing check has since started watching it
            too, since that check doesn&apos;t care when a position was opened — only whether the setting is
            currently on. For that position alone, both are live, and whichever fires first wins. Either way,
            the stop-loss above is a completely separate, always-on order — it doesn&apos;t know or care which
            take-profit mode is active, and it&apos;s watching every position from the moment it&apos;s bought.
          </li>
          <li>
            <span className="text-white">Sentiment-driven exit at -5 (Path A positions).</span> Independent of
            price, if the news sentiment on a stock the bot holds turns sharply negative (score of -5 or worse),
            it can sell early — it doesn&apos;t need to wait for the stop-loss to be hit.
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
            <span className="text-white">Market regime filter.</span> New buys pause on both paths when the
            broader market (SPY) is down more than 2% on the day or below its 50-day average — described in
            step 1 of Path A above.
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
        <div className="grid gap-3 md:grid-cols-2">
          <ExampleCard type="buy" title="Path A: good-enough beats perfect">
            A stock has 9 fresh headlines about a strong earnings beat (sentiment +6.0) — solid, but below the
            old hard cutoff of +8. Volume is a modest 1.4x. Under the old all-or-nothing rule, this sentiment
            score alone would have blocked the trade outright. Under the composite score, sentiment contributes
            0.30 (60% credit at 50% weight), headlines contribute the full 0.20 (9 headlines clears the 5-headline
            bar), and volume contributes 0.28 (93% credit at 30% weight) — a total of about 0.78, comfortably
            above the 0.60 bar. The bot buys, with a -10% stop-loss and +20% take-profit attached immediately.
          </ExampleCard>
          <ExampleCard type="sell" title="Path A: sentiment sours on a held position">
            The bot holds a stock bought two weeks ago on a momentum signal. New headlines about a product
            recall push sentiment to -6.5. The price hasn&apos;t yet dropped 10%, but because the sentiment rule
            (-5 threshold) is triggered independently, the bot sells early rather than waiting for the stop-loss.
          </ExampleCard>
          <ExampleCard type="sell" title="Trailing stop: letting a winner run, then locking it in">
            Bought at $100 with the trailing stop on, so no 20% flag was ever placed — just a distant $150
            backstop and a $90 stop-loss, both set the moment the trade opens. The price climbs: $102 (past the
            3% that arms the trailing check, so the bot now has a peak to trail), then $110, then $125, then a
            peak of $140. It slips back to $130 — a 7.1% pullback from that $140 peak, just past the 7%
            trigger — and the bot sells there, locking in a $30 (30%) gain. Note the $90 stop-loss was live the
            entire time, completely separately: if the price had instead dropped straight from $102 down to $90
            without ever climbing further, the stop-loss alone would have sold it there for a loss, regardless
            of the trailing stop never having anything to trail from. And had the price instead kept climbing
            straight to $150 without ever pulling back 7%, the bot would have kept holding all the way there.
          </ExampleCard>
          <ExampleCard type="hold" title="Path B: an oversold dip, logged not bought">
            A stock trading well above its 200-day average drops sharply over two days — RSI(2) reads 4.1, deep
            oversold territory — on 1.8x normal volume, with no particularly bad news behind it (sentiment
            -1.5, above the -5 veto). Every Path B condition is met. Because Path B is in shadow mode, the bot
            logs &quot;would BUY (reversion)&quot; with its full reasoning, but places no order — it&apos;s
            recorded for later review, not traded.
          </ExampleCard>
          <ExampleCard type="hold" title="Not enough to go on">
            A stock has only 2 headlines today, and they&apos;re mildly positive. That&apos;s below the minimum
            of 5 headlines needed for full credit, and its price is flat, so its composite score falls well
            short of 0.60. Evidence is too thin either way, so the bot holds and waits for the next cycle.
          </ExampleCard>
        </div>
      </Section>
    </div>
  );
}
