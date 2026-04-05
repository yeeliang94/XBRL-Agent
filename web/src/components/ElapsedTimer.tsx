import { useState, useEffect } from "react";
import { pwc } from "../lib/theme";

interface Props {
  startTime: number;
  isRunning: boolean;
}

function formatElapsed(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
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
        fontFamily: pwc.fontMono,
        fontSize: 14,
        color: pwc.grey700,
      }}
    >
      {formatElapsed(elapsed)}
    </span>
  );
}
