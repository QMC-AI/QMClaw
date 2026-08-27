/**
 * Workflow Flow - Main React Flow canvas component
 *
 * Simplified version with basic drag-drop, node selection, and execution support.
 */

"use client";

import { useCallback, useRef, useMemo, useState, useEffect } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  Connection,
  Node,
  Edge,
  BackgroundVariant,
  Panel,
  useReactFlow,
  ReactFlowProvider,
  applyNodeChanges,
  NodeChange,
  EdgeChange,
  useNodesState,
  useEdgesState,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';

import { useWorkflowStore, WorkflowNode, WorkflowNodeData } from '../../store/workflowStore';
import { nodeTypes } from './nodes';
import NodePalette from './controls/NodePalette';

interface Props {
  onRunWorkflow: () => void;
  onRunSelected: () => void;
  onStop: () => void;
  onSave: () => void;
  onConfigNode: (nodeId: string | null) => void;
  onAutoLayout?: () => void;
  onToggleSearch?: () => void;
  onExport?: () => void;
  onImport?: () => void;
  onLoad?: (workflowId: string) => void;
  onLog?: (msg: string, isError?: boolean) => void;
  selectedNodeId: string | null;
}

function WorkflowFlowInner({
  onRunWorkflow,
  onRunSelected,
  onStop,
  onSave,
  onConfigNode,
  onAutoLayout,
  onToggleSearch,
  onExport,
  onImport,
  onLoad,
  onLog,
  selectedNodeId,
}: Props) {
  const reactFlowWrapper = useRef<HTMLDivElement>(null);
  const { screenToFlowPosition, fitView } = useReactFlow();

  // Get state from store (for initial load and external updates)
  const storeNodes = useWorkflowStore((state) => state.nodes);
  const storeEdges = useWorkflowStore((state) => state.edges);
  const addNodeToStore = useWorkflowStore((state) => state.addNode);
  const selectNode = useWorkflowStore((state) => state.selectNode);
  const clearSelection = useWorkflowStore((state) => state.clearSelection);
  const setStoreNodes = useWorkflowStore((state) => state.setNodes);
  const setStoreEdges = useWorkflowStore((state) => state.setEdges);
  const addEdgeToStore = useWorkflowStore((state) => state.addEdge);
  const undo = useWorkflowStore((state) => state.undo);
  const redo = useWorkflowStore((state) => state.redo);
  const deleteNode = useWorkflowStore((state) => state.deleteNode);
  const deleteEdge = useWorkflowStore((state) => state.deleteEdge);

  // Initialize React Flow state from store (only on mount)
  const [initialLoad, setInitialLoad] = useState(true);
  const initialNodes = useMemo(() => storeNodes.map((node) => ({
    id: node.id,
    type: node.data.type,
    position: node.position,
    data: node.data,
    selected: node.selected || false,
  })), []);

  const initialEdges = useMemo(() => storeEdges.map((edge) => ({
    id: edge.id,
    source: edge.source,
    target: edge.target,
    sourceHandle: edge.sourceHandle,
    type: 'smoothstep',
    animated: edge.animated,
    selected: false,
  })), []);

  // React Flow internal state
  const [rfNodes, setRfNodes, onRfNodesChange] = useNodesState(initialNodes as Node[]);
  const [rfEdges, setRfEdges, onRfEdgesChange] = useEdgesState(initialEdges as Edge[]);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);

  // Refs to store latest nodes/edges (for use in syncToStore callbacks)
  const rfNodesLatest = useRef(rfNodes);
  const rfEdgesLatest = useRef(rfEdges);

  // Update refs whenever state changes
  rfNodesLatest.current = rfNodes;
  rfEdgesLatest.current = rfEdges;

  // Sync selected edge state to rfEdges
  const rfEdgesWithSelection = useMemo(() =>
    rfEdges.map((e) => ({
      ...e,
      selected: e.id === selectedEdgeId,
      style: e.id === selectedEdgeId
        ? { stroke: '#38bdf8', strokeWidth: 3 }
        : e.animated
          ? { stroke: '#22c55e', strokeWidth: 2 }
          : { stroke: '#64748b', strokeWidth: 2 },
    })),
    [rfEdges, selectedEdgeId]
  );

  // Mark initial load complete
  useEffect(() => {
    setInitialLoad(false);
    // Center the view when workflow first loads
    if (rfNodes.length > 0) {
      setTimeout(() => {
        fitView({ padding: 0.2, duration: 300 });
      }, 100);
    }
  }, []);

  // Also center when nodes are added from external sources (templates, etc.)
  useEffect(() => {
    if (!initialLoad && rfNodes.length > 0) {
      setTimeout(() => {
        fitView({ padding: 0.2, duration: 300 });
      }, 100);
    }
  }, [rfNodes.length]);

  // Sync store changes to React Flow state (for external adds like templates and auto-layout)
  useEffect(() => {
    if (initialLoad) return; // Skip on initial load, useNodesState handles that

    const storeNodeIds = new Set(storeNodes.map(n => n.id));
    const rfNodeIds = new Set(rfNodes.map(n => n.id));

    // Find new nodes (in store but not in RF)
    const newNodes = storeNodes
      .filter(n => !rfNodeIds.has(n.id))
      .map(node => ({
        id: node.id,
        type: node.data.type,
        position: node.position,
        data: node.data,
        selected: node.selected || false,
      }));

    // Update positions and config for existing nodes
    const positionChanged = storeNodes.some(storeNode => {
      const rfNode = rfNodes.find(n => n.id === storeNode.id);
      if (!rfNode) return false;
      return rfNode.position.x !== storeNode.position.x || rfNode.position.y !== storeNode.position.y;
    });

    // Check if config has changed for any node
    const configChanged = storeNodes.some(storeNode => {
      const rfNode = rfNodes.find(n => n.id === storeNode.id);
      if (!rfNode) return false;
      return JSON.stringify(rfNode.data.config) !== JSON.stringify(storeNode.data.config);
    });

    if (newNodes.length > 0) {
      setRfNodes(nds => [...nds, ...newNodes]);
    } else if (positionChanged || configChanged) {
      // Update positions and config for all nodes
      setRfNodes(nds => nds.map(rfNode => {
        const storeNode = storeNodes.find(n => n.id === rfNode.id);
        if (storeNode) {
          return {
            ...rfNode,
            position: storeNode.position,
            data: { ...rfNode.data, config: storeNode.data.config },
          };
        }
        return rfNode;
      }));
    }
  }, [storeNodes, initialLoad]);

  // Sync store edges to React Flow state (for imports and template adds)
  useEffect(() => {
    if (initialLoad) return; // Skip on initial load

    const storeEdgeIds = new Set(storeEdges.map(e => e.id));
    const rfEdgeIds = new Set(rfEdges.map(e => e.id));

    // Find new edges (in store but not in RF)
    const newEdges = storeEdges
      .filter(e => !rfEdgeIds.has(e.id))
      .map(edge => ({
        id: edge.id,
        source: edge.source,
        target: edge.target,
        sourceHandle: edge.sourceHandle,
        type: edge.type || 'smoothstep',
        animated: edge.animated || false,
        selected: false,
      }));

    if (newEdges.length > 0) {
      setRfEdges(eds => [...eds, ...newEdges]);
    }
  }, [storeEdges, initialLoad]);

  // Sync React Flow state to store (on changes)
  const syncToStore = useCallback(() => {
    const nodes = rfNodesLatest.current.map((n) => ({
      id: n.id,
      position: n.position,
      data: {
        ...n.data,
        selected: n.selected,
      },
    })) as unknown as WorkflowNode[];
    const edges = rfEdgesLatest.current.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      sourceHandle: e.sourceHandle,
      type: 'smoothstep' as const,
      animated: e.animated || false,
    }));
    setStoreNodes(nodes);
    setStoreEdges(edges as unknown as any);
  }, [setStoreNodes, setStoreEdges]);

  // Force sync from React Flow to store when requested (e.g., before running workflow)
  useEffect(() => {
    const handleForceSync = () => {
      // Call syncToStore synchronously and wait for state update
      syncToStore();
      // Also flush React state to ensure we have latest data
      setRfNodes(nds => nds);
      setRfEdges(eds => eds);
    };
    window.addEventListener('qmclaw:force-sync', handleForceSync);
    return () => window.removeEventListener('qmclaw:force-sync', handleForceSync);
  }, [syncToStore]);

  // Handle node changes from React Flow
  const onNodesChange = useCallback(
    (changes: NodeChange[]) => {
      onRfNodesChange(changes as any);
      // Sync to store after React Flow state updates (deferred)
      setTimeout(syncToStore, 0);
    },
    [onRfNodesChange, syncToStore]
  );

  // Handle edge changes from React Flow
  const onEdgesChange = useCallback(
    (changes: EdgeChange[]) => {
      onRfEdgesChange(changes as any);
      // Sync to store after React Flow state updates (deferred)
      setTimeout(syncToStore, 0);
    },
    [onRfEdgesChange, syncToStore]
  );

  // Handle connection
  const onConnect = useCallback(
    (params: Connection) => {
      const newEdge = {
        id: `e_${params.source}_${params.target}_${Date.now()}`,
        source: params.source!,
        target: params.target!,
        sourceHandle: params.sourceHandle || undefined,
        type: 'smoothstep',
        animated: false,
      };
      setRfEdges((eds) => {
        const updatedEdges = [...eds, newEdge];
        // Update ref immediately so syncToStore has the latest
        rfEdgesLatest.current = updatedEdges;
        return updatedEdges;
      });
      // Also sync nodes since onConnect may be called after node drag
      rfNodesLatest.current = rfNodes;
      // Now sync to store
      syncToStore();
    },
    [setRfEdges, syncToStore, rfNodes]
  );

  // Handle node click
  const onNodeClick = useCallback(
    (_: any, node: Node) => {
      selectNode(node.id);
      // Also select in React Flow state
      setRfNodes((nds) =>
        nds.map((n) => ({
          ...n,
          selected: n.id === node.id,
        }))
      );
    },
    [selectNode, setRfNodes]
  );

  // Handle pane click (deselect)
  const onPaneClick = useCallback(() => {
    clearSelection();
    onConfigNode(null);
    // Also deselect all in React Flow
    setRfNodes((nds) => nds.map((n) => ({ ...n, selected: false })));
    setSelectedEdgeId(null);
  }, [clearSelection, onConfigNode, setRfNodes, setSelectedEdgeId]);

  // Handle node double click (open config)
  const onNodeDoubleClick = useCallback(
    (_: any, node: Node) => {
      onConfigNode(node.id);
    },
    [onConfigNode]
  );

  // Handle edge click (select edge)
  const onEdgeClick = useCallback(
    (_: any, edge: Edge) => {
      // Deselect all nodes
      setRfNodes((nds) => nds.map((n) => ({ ...n, selected: false })));
      // Select this edge
      setSelectedEdgeId(edge.id);
      clearSelection();
      onConfigNode(null);
    },
    [setRfNodes, clearSelection, onConfigNode]
  );

  // Handle keyboard shortcuts
  const onKeyDown = useCallback(
    (event: React.KeyboardEvent) => {
      // Delete selected nodes or edge
      if (event.key === 'Delete' || event.key === 'Backspace') {
        // Check if an edge is selected
        if (selectedEdgeId) {
          // Delete selected edge
          setRfEdges((eds) => eds.filter((e) => e.id !== selectedEdgeId));
          deleteEdge(selectedEdgeId);
          setSelectedEdgeId(null);
        } else {
          // Delete selected nodes
          const selectedIds = rfNodes.filter((n) => n.selected).map((n) => n.id);
          // Delete from React Flow state
          setRfNodes((nds) => nds.filter((n) => !n.selected));
          setRfEdges((eds) => eds.filter((e) => !selectedIds.includes(e.source) && !selectedIds.includes(e.target)));
          // Delete from store
          selectedIds.forEach((id) => deleteNode(id));
        }
      }

      // Undo
      if ((event.key === 'z' || event.key === 'Z') && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        undo();
      }

      // Redo
      if ((event.key === 'y' || event.key === 'Y') && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        redo();
      }

      // Copy (Ctrl+D) - duplicate selected node
      if ((event.key === 'd' || event.key === 'D') && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        const selectedNode = rfNodes.find((n) => n.selected);
        if (selectedNode) {
          const newId = `${selectedNode.type}_${Date.now()}`;
          const newNode = {
            ...selectedNode,
            id: newId,
            position: {
              x: selectedNode.position.x + 50,
              y: selectedNode.position.y + 50,
            },
            selected: false,
          };
          setRfNodes((nds) => [...nds, newNode]);
        }
      }

      // Run selected (Ctrl+Enter)
      if ((event.key === 'Enter') && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        const selected = rfNodes.filter((n) => n.selected);
        if (selected.length === 1) {
          onRunSelected();
        }
      }

      // Escape
      if (event.key === 'Escape') {
        clearSelection();
        onConfigNode(null);
        // Also deselect in React Flow
        setRfNodes((nds) => nds.map((n) => ({ ...n, selected: false })));
        setSelectedEdgeId(null);
      }
    },
    [selectedEdgeId, deleteNode, deleteEdge, undo, redo, clearSelection, onConfigNode, onRunSelected, setRfNodes, setRfEdges, setSelectedEdgeId]
  );

  // Handle drop for new nodes - add to both React Flow state and store
  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();

      const type = event.dataTransfer.getData('application/reactflow');
      if (!type) return;

      const position = screenToFlowPosition({
        x: event.clientX,
        y: event.clientY,
      });

      // Add to Zustand store
      const newNode = addNodeToStore(type as any, position);

      // Also add directly to React Flow state
      if (newNode) {
        setRfNodes((nds) => [
          ...nds,
          {
            id: newNode.id,
            type: newNode.data.type,
            position: newNode.position,
            data: newNode.data,
            selected: false,
          },
        ]);
      }
    },
    [screenToFlowPosition, addNodeToStore, setRfNodes]
  );

  // Handle drag over
  const onDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  }, []);

  return (
    <div
      ref={reactFlowWrapper}
      style={{ width: '100%', height: '100%' }}
      onKeyDown={onKeyDown}
      tabIndex={0}
    >
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdgesWithSelection}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onDrop={onDrop}
        onDragOver={onDragOver}
        onNodeClick={onNodeClick}
        onNodeDoubleClick={onNodeDoubleClick}
        onEdgeClick={onEdgeClick}
        onPaneClick={onPaneClick}
        nodeTypes={nodeTypes as any}
        snapToGrid
        snapGrid={[15, 15]}
        defaultEdgeOptions={{
          type: 'smoothstep',
          animated: false,
          style: { stroke: '#64748b', strokeWidth: 2 },
        }}
        style={{
          background: '#0a0f1a',
        }}
        proOptions={{ hideAttribution: true }}
      >
        <Background
          variant={BackgroundVariant.Dots}
          gap={20}
          size={1}
          color="#1e293b"
        />
        <Controls
          style={{
            background: '#1e293b',
            border: '1px solid #334155',
            borderRadius: '8px',
          }}
        />
        <MiniMap
          nodeColor={(node: any) => {
            const status = node.data?.status;
            switch (status) {
              case 'running': return '#38bdf8';
              case 'completed': return '#22c55e';
              case 'failed': return '#f87171';
              default: return '#475569';
            }
          }}
          maskColor="rgba(0,0,0,0.8)"
          style={{
            background: '#0f172a',
            border: '1px solid #1e293b',
            borderRadius: '8px',
          }}
        />

        {/* Node Palette */}
        <Panel position="top-left">
          <NodePalette />
        </Panel>

        {/* Empty state */}
        {storeNodes.length === 0 && (
          <div style={{
            position: 'absolute',
            top: '50%',
            left: '50%',
            transform: 'translate(-50%, -50%)',
            textAlign: 'center',
            color: '#475569',
            padding: '40px',
          }}>
            <div style={{ fontSize: '48px', marginBottom: '16px' }}>📋</div>
            <div style={{ fontSize: '16px', fontWeight: 600, marginBottom: '8px' }}>
              Empty Canvas
            </div>
            <div style={{ fontSize: '14px' }}>
              Drag nodes from the palette to get started
            </div>
          </div>
        )}
      </ReactFlow>
    </div>
  );
}

// Wrapper with ReactFlowProvider
export default function WorkflowFlow(props: Props) {
  return (
    <ReactFlowProvider>
      <WorkflowFlowInner {...props} />
    </ReactFlowProvider>
  );
}
