/**
 * The handful of glyphs this island needs, inline. Not an icon package: six
 * shapes do not justify a dependency, and every one of these is small enough
 * that hand-drawn paths cost less bundle size than an import would.
 */

type IconProps = { size?: number; className?: string };

const base = (size: number) => ({
  width: size,
  height: size,
  viewBox: '0 0 16 16',
  fill: 'none',
  'aria-hidden': true as const,
  focusable: false as const,
});

export function IconCheck({ size = 16, className }: IconProps) {
  return (
    <svg {...base(size)} className={className}>
      <path d="M3 8.5L6.2 11.7L13 4.5" stroke="currentColor" strokeWidth="1.6"
        strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function IconPencil({ size = 16, className }: IconProps) {
  return (
    <svg {...base(size)} className={className}>
      <path d="M10.6 2.4l3 3L4.4 14.6 1 15l0.4-3.4L10.6 2.4z" stroke="currentColor"
        strokeWidth="1.3" strokeLinejoin="round" />
    </svg>
  );
}

export function IconTrash({ size = 16, className }: IconProps) {
  return (
    <svg {...base(size)} className={className}>
      <path d="M3 4.5h10M6.2 4.5V3a1 1 0 011-1h1.6a1 1 0 011 1v1.5M4.3 4.5l.6 9a1 1 0 001 .9h4.2a1 1 0 001-.9l.6-9"
        stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function IconChevronDown({ size = 16, className }: IconProps) {
  return (
    <svg {...base(size)} className={className}>
      <path d="M4 6l4 4 4-4" stroke="currentColor" strokeWidth="1.5"
        strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function IconClose({ size = 16, className }: IconProps) {
  return (
    <svg {...base(size)} className={className}>
      <path d="M3.5 3.5l9 9m0-9l-9 9" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

export function IconSpinner({ size = 16, className }: IconProps) {
  return (
    <svg {...base(size)} className={`pw-spin ${className ?? ''}`}>
      <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="1.6" opacity="0.25" />
      <path d="M14 8a6 6 0 00-6-6" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}
