// Lucide "ban" with the lucide-animated draw-on-hover effect, done in CSS so the
// bundle carries no animation runtime. Hover the nearest `.group` ancestor to play it.
export default function BanIcon({ size = 17, className = '' }) {
  return (
    <svg
      className={`ban-icon ${className}`.trim()}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="10" pathLength="1" />
      <path d="m4.9 4.9 14.2 14.2" pathLength="1" />
    </svg>
  )
}
