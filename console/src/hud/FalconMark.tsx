// An original, minimal falcon: a front-on bird with swept, layered wing tips and a notched tail,
// drawn by hand on a 100x100 grid in currentColor so it takes the HUD's phase colour.
// Three parts so it can move: each wing is its own group, pivoting at its shoulder, and the body
// (head, keel, tail) is the third. Wings are drawn first, behind the body. The poses are in hud.css.

// Shoulders in the falcon's own 100x100 units (hud.css uses transform-box: view-box for the wings).
const SHOULDER_L = { transformOrigin: "45px 38px" };
const SHOULDER_R = { transformOrigin: "55px 38px" };

export function FalconPaths() {
  return (
    <g className="falcon-parts" fill="currentColor" fillOpacity={0.16} stroke="currentColor" strokeWidth={1.6} strokeLinejoin="round" strokeLinecap="round">
      <g className="wing wing-l" style={SHOULDER_L}>
        <path d="M45 33 L6 50 L19 52.5 L8 60 L23 59.5 L15 67 L35 58 L45 47 Z" />
        {/* primary feather lines */}
        <path d="M40 40 L19 52.5 M38 47 L23 59.5" fill="none" strokeOpacity={0.5} strokeWidth={1} />
      </g>
      <g className="wing wing-r" style={SHOULDER_R}>
        <path d="M55 33 L94 50 L81 52.5 L92 60 L77 59.5 L85 67 L65 58 L55 47 Z" />
        <path d="M60 40 L81 52.5 M62 47 L77 59.5" fill="none" strokeOpacity={0.5} strokeWidth={1} />
      </g>
      <g className="falcon-body">
        {/* body and head, with a short hooked beak */}
        <path d="M50 16 L56 26 L55.5 55 L50 68 L44.5 55 L44 26 Z" fillOpacity={0.3} />
        <path d="M48.6 19.5 L50 23 L51.4 19.5" fill="none" strokeWidth={1.1} />
        {/* notched tail */}
        <path d="M46.5 64 L44 84 L50 79.5 L56 84 L53.5 64" fillOpacity={0.22} />
        {/* eye stripes (the kestrel's moustache marks) */}
        <path d="M47.2 24.5 L46.2 29.5 M52.8 24.5 L53.8 29.5" fill="none" strokeWidth={1.2} />
        {/* eyes: hidden except in Standby, where they glow softly (hud.css) */}
        <circle className="eye" cx={47.9} cy={22.4} r={1.2} stroke="none" />
        <circle className="eye" cx={52.1} cy={22.4} r={1.2} stroke="none" />
        {/* keel line */}
        <path d="M50 31 L50 58" fill="none" strokeOpacity={0.55} strokeWidth={1} />
      </g>
    </g>
  );
}

export function FalconMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 100 100" className={className} aria-hidden="true">
      <FalconPaths />
    </svg>
  );
}
