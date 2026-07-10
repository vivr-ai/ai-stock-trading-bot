export default function StrategyFlowDiagram() {
  const box = "fill-bg-panel2 stroke-bg-border";
  const outcomeBoxClass = (tone: "gain" | "loss" | "neutral") =>
    tone === "gain" ? "fill-gain/10 stroke-gain" : tone === "loss" ? "fill-loss/10 stroke-loss" : "fill-bg-panel2 stroke-bg-border";

  return (
    <svg viewBox="0 0 700 700" className="w-full" role="img" aria-label="Flow diagram of how the bot makes a trade decision">
      <defs>
        <marker id="arrow" markerWidth="8" markerHeight="8" refX="4" refY="4" orient="auto">
          <path d="M0,0 L8,4 L0,8 Z" fill="#8592a8" />
        </marker>
      </defs>

      {/* connecting lines */}
      <line x1="350" y1="70" x2="350" y2="93" stroke="#8592a8" strokeWidth="1.5" markerEnd="url(#arrow)" />
      <line x1="350" y1="150" x2="350" y2="173" stroke="#8592a8" strokeWidth="1.5" markerEnd="url(#arrow)" />
      <line x1="350" y1="230" x2="350" y2="253" stroke="#8592a8" strokeWidth="1.5" markerEnd="url(#arrow)" />
      <line x1="350" y1="310" x2="350" y2="333" stroke="#8592a8" strokeWidth="1.5" markerEnd="url(#arrow)" />
      <line x1="350" y1="390" x2="350" y2="405" stroke="#8592a8" strokeWidth="1.5" />

      {/* fan-out to 3 outcomes */}
      <path d="M350,405 L110,405 L110,418" fill="none" stroke="#8592a8" strokeWidth="1.5" markerEnd="url(#arrow)" />
      <path d="M350,405 L350,418" fill="none" stroke="#8592a8" strokeWidth="1.5" markerEnd="url(#arrow)" />
      <path d="M350,405 L590,405 L590,418" fill="none" stroke="#8592a8" strokeWidth="1.5" markerEnd="url(#arrow)" />

      {/* buy -> bracket order */}
      <line x1="590" y1="480" x2="590" y2="498" stroke="#8592a8" strokeWidth="1.5" markerEnd="url(#arrow)" />

      {/* converge to wait node */}
      <path d="M110,480 L110,590 L340,590" fill="none" stroke="#8592a8" strokeWidth="1.5" />
      <path d="M350,480 L350,600" fill="none" stroke="#8592a8" strokeWidth="1.5" markerEnd="url(#arrow)" />
      <path d="M590,555 L590,590 L360,590" fill="none" stroke="#8592a8" strokeWidth="1.5" markerEnd="url(#arrow)" />

      {/* loop back */}
      <path
        d="M475,625 C 640,625 640,45 475,45"
        fill="none"
        stroke="#8592a8"
        strokeWidth="1.5"
        strokeDasharray="4 3"
        markerEnd="url(#arrow)"
      />
      <text x="600" y="335" fontSize="10" fill="#8592a8" textAnchor="middle">loops back</text>

      {/* node 1 */}
      <rect x="200" y="20" width="300" height="50" rx="10" className={box} />
      <text x="350" y="40" textAnchor="middle" fontSize="13" fill="#e6e9ef">Every 30 minutes</text>
      <text x="350" y="58" textAnchor="middle" fontSize="13" fill="#e6e9ef">during market hours</text>

      {/* node 2 */}
      <rect x="150" y="100" width="400" height="50" rx="10" className={box} />
      <text x="350" y="120" textAnchor="middle" fontSize="13" fill="#e6e9ef">Read today's news</text>
      <text x="350" y="138" textAnchor="middle" fontSize="13" fill="#e6e9ef">for each stock it watches</text>

      {/* node 3 */}
      <rect x="150" y="180" width="400" height="50" rx="10" className={box} />
      <text x="350" y="200" textAnchor="middle" fontSize="13" fill="#e6e9ef">Score the sentiment</text>
      <text x="350" y="218" textAnchor="middle" fontSize="13" fill="#e6e9ef">-10 (very bad) to +10 (very good)</text>

      {/* node 4 */}
      <rect x="150" y="260" width="400" height="50" rx="10" className={box} />
      <text x="350" y="280" textAnchor="middle" fontSize="13" fill="#e6e9ef">Double-check with</text>
      <text x="350" y="298" textAnchor="middle" fontSize="13" fill="#e6e9ef">price trend & trading volume</text>

      {/* node 5 */}
      <rect x="150" y="340" width="400" height="50" rx="10" className={box} />
      <text x="350" y="360" textAnchor="middle" fontSize="13" fill="#e6e9ef">Apply risk rules:</text>
      <text x="350" y="378" textAnchor="middle" fontSize="13" fill="#e6e9ef">position size, sector & cash limits</text>

      {/* outcome: sell */}
      <rect x="35" y="418" width="150" height="62" rx="10" className={outcomeBoxClass("loss")} />
      <text x="110" y="438" textAnchor="middle" fontSize="12" fill="#e6e9ef">Held it, and</text>
      <text x="110" y="454" textAnchor="middle" fontSize="12" fill="#e6e9ef">news turned bad</text>
      <text x="110" y="472" textAnchor="middle" fontSize="13" fontWeight="600" fill="#ef4444">→ SELL</text>

      {/* outcome: hold */}
      <rect x="275" y="418" width="150" height="62" rx="10" className={outcomeBoxClass("neutral")} />
      <text x="350" y="438" textAnchor="middle" fontSize="12" fill="#e6e9ef">Mixed evidence, or</text>
      <text x="350" y="454" textAnchor="middle" fontSize="12" fill="#e6e9ef">a rule blocks it</text>
      <text x="350" y="472" textAnchor="middle" fontSize="13" fontWeight="600" fill="#8592a8">→ HOLD</text>

      {/* outcome: buy */}
      <rect x="515" y="418" width="150" height="62" rx="10" className={outcomeBoxClass("gain")} />
      <text x="590" y="438" textAnchor="middle" fontSize="12" fill="#e6e9ef">Strongly positive</text>
      <text x="590" y="454" textAnchor="middle" fontSize="12" fill="#e6e9ef">& confirmed</text>
      <text x="590" y="472" textAnchor="middle" fontSize="13" fontWeight="600" fill="#22c55e">→ BUY</text>

      {/* bracket order box, under buy */}
      <rect x="490" y="498" width="200" height="57" rx="10" className={box} />
      <text x="590" y="518" textAnchor="middle" fontSize="12" fill="#e6e9ef">Auto stop-loss -10%</text>
      <text x="590" y="536" textAnchor="middle" fontSize="12" fill="#e6e9ef">take-profit +20%</text>

      {/* wait node */}
      <rect x="225" y="600" width="250" height="50" rx="10" className={box} />
      <text x="350" y="620" textAnchor="middle" fontSize="13" fill="#e6e9ef">Wait for the next cycle</text>
      <text x="350" y="638" textAnchor="middle" fontSize="13" fill="#e6e9ef">(~30 minutes)</text>
    </svg>
  );
}
