import * as React from "react"

export interface ProgressProps extends React.HTMLAttributes<HTMLDivElement> {
  value?: number;
}

const Progress = React.forwardRef<HTMLDivElement, ProgressProps>(
  ({ className, value = 0, ...props }, ref) => {
    const classes = ["progress", className].filter(Boolean).join(" ");

    return (
      <div ref={ref} className={classes} {...props}>
        <div
          className="progress-bar"
          style={{ width: `${Math.min(100, Math.max(0, value))}%` }}
        />
      </div>
    )
  }
)
Progress.displayName = "Progress"

export { Progress }
