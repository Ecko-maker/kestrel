// An original, minimal falcon emblem: a front-on stoop with swept, layered wing tips and a notched tail.
// Drawn on a 100x100 grid in currentColor, so it takes the HUD's phase colour.

export function FalconPaths() {
  return (
    <g fill="currentColor" fillOpacity={0.16} stroke="currentColor" strokeWidth={1.6} strokeLinejoin="round" strokeLinecap="round">
      {/* wings: three feather tips each side */}
      <path d="M45 33 L6 50 L19 52.5 L8 60 L23 59.5 L15 67 L35 58 L45 47 Z" />
      <path d="M55 33 L94 50 L81 52.5 L92 60 L77 59.5 L85 67 L65 58 L55 47 Z" />
      {/* body and head */}
      <path d="M50 16 L56 26 L55.5 55 L50 68 L44.5 55 L44 26 Z" fillOpacity={0.3} />
      {/* notched tail */}
      <path d="M46.5 64 L44 84 L50 79.5 L56 84 L53.5 64" fillOpacity={0.22} />
      {/* eye stripes (the kestrel's moustache marks) */}
      <path d="M47.2 24.5 L46.2 29.5 M52.8 24.5 L53.8 29.5" fill="none" strokeWidth={1.2} />
      {/* keel line */}
      <path d="M50 31 L50 58" fill="none" strokeOpacity={0.55} strokeWidth={1} />
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
