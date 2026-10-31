import { useState, useEffect } from "react";
import { ds } from "../lib/theme";
import { formatElapsedMs } from "../lib/time";

interface Props {
  startTime: number;
  isRunning: boolean;
}

export function ElapsedTimer({ startTime, isRunning }: Props) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!isRunning) return;

    // Update immediately, then every second
    setElapsed(Date.now() - startTime);

    const id = setInterval(() => {
      setElapsed(Date.now() - startTime);
    }, 1000);

    return () => clearInterval(id);
  }, [isRunning, startTime]);

  return (
    <span
      style={{
        fontFamily: ds.fontMono,
        fontSize: 14,
        color: ds.grey700,
      }}
    >
      {formatElapsedMs(elapsed)}
    </span>
  );
}
