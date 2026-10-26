import { useEffect, useRef } from "react";
import { World } from "../world/World";

export function GameView() {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const world = new World(ref.current!);
    if (import.meta.env.DEV) (window as unknown as { __world: World }).__world = world;
    return () => world.dispose();
  }, []);

  return <div ref={ref} className="game" />;
}
