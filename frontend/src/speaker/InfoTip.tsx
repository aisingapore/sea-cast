import type { ReactNode } from "react";

/**
 * A small "ⓘ" badge that shows a tooltip on hover or keyboard focus. Screen
 * readers get `label` (the tooltip itself is hidden from them). The tooltip
 * shows `label` too, unless `children` gives richer content for it.
 */
export function InfoTip({ label, children }: { label: string; children?: ReactNode }) {
  return (
    <span
      className="info-badge"
      role="img"
      tabIndex={0}
      aria-label={label}
      // Inside a <label>, a click would focus the control; block that so the
      // badge only shows the tooltip.
      onMouseDown={(e) => e.preventDefault()}
    >
      <span aria-hidden="true">ⓘ</span>
      <span className="info-tooltip" aria-hidden="true">
        {children ?? label}
      </span>
    </span>
  );
}
