import * as React from "react"

function Skeleton({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  const classes = ["skeleton", className].filter(Boolean).join(" ");

  return (
    <div
      className={classes}
      {...props}
    />
  )
}

export { Skeleton }
