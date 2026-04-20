import { describe, test, expect } from "vitest";
import { parseSSEStream, type RawSSEEvent } from "../lib/sse";

// Build a ReadableStream that yields the given UTF-8 chunks in order. Each
// chunk is delivered as a separate `read()` call, letting us test partial-line
// handling across chunk boundaries — the single realistic failure mode of
// a raw-bytes streaming parser.
function makeReader(chunks: string[]): ReadableStreamDefaultReader<Uint8Array> {
  const encoder = new TextEncoder();
  let i = 0;
  return {
    async read() {
      if (i >= chunks.length) return { done: true, value: undefined };
      return { done: false, value: encoder.encode(chunks[i++]) };
    },
    releaseLock() {},
    closed: Promise.resolve(undefined),
    cancel: async () => {},
  } as unknown as ReadableStreamDefaultReader<Uint8Array>;
}

async function collect(reader: ReadableStreamDefaultReader<Uint8Array>): Promise<RawSSEEvent[]> {
  const events: RawSSEEvent[] = [];
  for await (const e of parseSSEStream(reader)) events.push(e);
  return events;
}

describe("parseSSEStream", () => {
  test("parses a single complete event in one chunk", async () => {
    const events = await collect(makeReader([
      `event: status\ndata: {"phase":"starting"}\n\n`,
    ]));
    expect(events).toHaveLength(1);
    expect(events[0].event).toBe("status");
    expect(events[0].data).toEqual({ phase: "starting" });
  });

  test("handles two events in one chunk", async () => {
    const events = await collect(makeReader([
      `event: status\ndata: {"phase":"starting"}\n\nevent: complete\ndata: {"ok":true}\n\n`,
    ]));
    expect(events.map((e) => e.event)).toEqual(["status", "complete"]);
  });

  test("reassembles partial lines across chunk boundaries", async () => {
    // Split the event across two reads mid-JSON to force the parser to hold
    // unfinished bytes in its buffer until the next chunk arrives.
    const events = await collect(makeReader([
      `event: status\ndata: {"pha`,
      `se":"starting"}\n\n`,
    ]));
    expect(events).toHaveLength(1);
    expect(events[0].data).toEqual({ phase: "starting" });
  });

  test("accepts `event:` with no trailing space", async () => {
    // Per spec both `event:name` and `event: name` are valid — some servers
    // omit the space. The parser must accept either form (#1).
    const events = await collect(makeReader([
      `event:status\ndata:{"phase":"starting"}\n\n`,
    ]));
    expect(events).toHaveLength(1);
    expect(events[0].event).toBe("status");
  });

  test("ignores SSE comment / heartbeat lines (#2)", async () => {
    const events = await collect(makeReader([
      `: keepalive ping\n\nevent: status\ndata: {"phase":"starting"}\n\n`,
    ]));
    expect(events).toHaveLength(1);
    expect(events[0].event).toBe("status");
  });

  test("blank lines reset the in-progress event state (#3)", async () => {
    // An `event:` followed by a blank line (no `data:`) would leave a stale
    // event name hanging. We reset on blanks so a later bare `data:` does
    // NOT accidentally get tagged with the previous event name.
    const events = await collect(makeReader([
      `event: status\n\ndata: {"phase":"orphaned"}\n\n`,
    ]));
    // The first data line has no event name (state was reset), so it's dropped.
    expect(events).toHaveLength(0);
  });

  test("skips one malformed JSON event but keeps the stream alive", async () => {
    const events = await collect(makeReader([
      `event: status\ndata: {"phase":"starting"}\n\n`,
      `event: status\ndata: not-json\n\n`,
      `event: complete\ndata: {"ok":true}\n\n`,
    ]));
    // Three events in, two come out — the malformed one is skipped.
    expect(events.map((e) => e.event)).toEqual(["status", "complete"]);
  });

  test("tolerates CRLF line endings", async () => {
    const events = await collect(makeReader([
      `event: status\r\ndata: {"phase":"starting"}\r\n\r\n`,
    ]));
    expect(events).toHaveLength(1);
    expect(events[0].event).toBe("status");
  });

  test("emits unknown event types — consumer is responsible for filtering", async () => {
    // The parser is generic across the multi-agent endpoint and scout, which
    // have different event vocabularies. It must pass everything through and
    // let the caller decide what to consume.
    const events = await collect(makeReader([
      `event: scout_complete\ndata: {"success":true}\n\nevent: status\ndata: {"phase":"ok"}\n\n`,
    ]));
    expect(events.map((e) => e.event)).toEqual(["scout_complete", "status"]);
  });
});
