/**
 * Auto Layout - Automatically arrange nodes in a hierarchical layout
 */

import { useCallback } from 'react';
import { useWorkflowStore } from './workflowStore';

/**
 * Compute a hierarchical layout for the workflow nodes.
 *
 * Uses a simple topological sort + layer-based positioning:
 * - Level 0: nodes with no dependencies
 * - Level N: nodes depending on level N-1
 *
 * Nodes within the same level are distributed horizontally.
 */
export function useAutoLayout() {
  const nodes = useWorkflowStore((state) => state.nodes);
  const edges = useWorkflowStore((state) => state.edges);
  const setNodes = useWorkflowStore((state) => state.setNodes);

  const computeLayout = useCallback(() => {
    if (nodes.length === 0) return;

    // Build adjacency info
    const incomingEdges = new Map<string, string[]>();
    const outgoingEdges = new Map<string, string[]>();
    nodes.forEach(n => {
      incomingEdges.set(n.id, []);
      outgoingEdges.set(n.id, []);
    });
    edges.forEach(e => {
      if (incomingEdges.has(e.target)) {
        incomingEdges.get(e.target)!.push(e.source);
      }
      if (outgoingEdges.has(e.source)) {
        outgoingEdges.get(e.source)!.push(e.target);
      }
    });

    // BFS to assign levels
    const levels = new Map<string, number>();
    const queue: string[] = [];

    // Start with nodes that have no incoming edges
    nodes.forEach(n => {
      if ((incomingEdges.get(n.id) || []).length === 0) {
        levels.set(n.id, 0);
        queue.push(n.id);
      }
    });

    while (queue.length > 0) {
      const current = queue.shift()!;
      const currentLevel = levels.get(current) || 0;
      const successors = outgoingEdges.get(current) || [];
      successors.forEach(succ => {
        const succIncoming = incomingEdges.get(succ) || [];
        const allPredLevels = succIncoming.map(p => levels.get(p) || 0);
        if (allPredLevels.every(l => l !== undefined)) {
          const newLevel = Math.max(...allPredLevels) + 1;
          const currentLevel = levels.get(succ);
          if (currentLevel === undefined || newLevel > currentLevel) {
            levels.set(succ, newLevel);
            queue.push(succ);
          }
        }
      });
    }

    // Handle nodes with circular deps or unconnected
    nodes.forEach(n => {
      if (!levels.has(n.id)) {
        levels.set(n.id, 0);
      }
    });

    // Group by level
    const byLevel = new Map<number, string[]>();
    levels.forEach((level, id) => {
      if (!byLevel.has(level)) byLevel.set(level, []);
      byLevel.get(level)!.push(id);
    });

    // Layout parameters
    const X_SPACING = 220;
    const Y_SPACING = 140;
    const START_X = 100;
    const START_Y = 100;

    // Compute positions
    const positions = new Map<string, { x: number; y: number }>();
    const sortedLevels = Array.from(byLevel.keys()).sort((a, b) => a - b);

    sortedLevels.forEach(level => {
      const nodesAtLevel = byLevel.get(level) || [];
      nodesAtLevel.forEach((id, index) => {
        positions.set(id, {
          x: START_X + index * X_SPACING,
          y: START_Y + level * Y_SPACING,
        });
      });
    });

    // Apply positions
    const newNodes = nodes.map(n => ({
      ...n,
      position: positions.get(n.id) || n.position,
    }));

    setNodes(newNodes);
  }, [nodes, edges, setNodes]);

  return { computeLayout };
}