import * as React from "react"

export interface BadgeProps extends React.HTMLAttributes<HTMLDivElement> {
  variant?: "default" | "secondary" | "destructive" | "outline";
}

function Badge({ className, variant = "default", ...props }: BadgeProps) {
  const variantClass = variant !== "default" ? `badge-${variant}` : "badge-default";
  const classes = ["badge", variantClass, className].filter(Boolean).join(" ");

  return (
    <div
      className={classes}
      data-variant={variant}
      {...props}
    />
  )
}

export { Badge }
