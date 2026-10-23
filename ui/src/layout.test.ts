import { describe, expect, it } from "vitest";
import { chooseDirection } from "./layout";

describe("layout direction", () => {
  it("depends only on the graph's proportions, not the window", () => {
    // a long chain: left-to-right is very wide, top-to-bottom fits better
    expect(chooseDirection({ width: 4000, height: 120 }, { width: 260, height: 1600 })).toBe("DOWN");
    // a wide fan-out: left-to-right fits better
    expect(chooseDirection({ width: 900, height: 700 }, { width: 2400, height: 400 })).toBe("RIGHT");
    // near ties stay left-to-right
    expect(chooseDirection({ width: 1000, height: 600 }, { width: 950, height: 620 })).toBe("RIGHT");
  });
});
