/**
 * Model Store - Zustand store for LLM model registry
 */

import { create } from 'zustand';
import { api } from '../lib/api';
import type { LLMModel } from '../types/model';

interface ModelStore {
  models: LLMModel[];
  isLoading: boolean;
  error: string | null;

  // Actions
  fetchModels: () => Promise<void>;
  addModel: (model: Omit<LLMModel, 'id' | 'createdAt' | 'updatedAt'>) => Promise<LLMModel>;
  updateModel: (id: string, data: Partial<LLMModel>) => Promise<void>;
  deleteModel: (id: string) => Promise<void>;
  getModelsByCapability: (capability: 'text' | 'vision') => LLMModel[];
  getEnabledModels: () => LLMModel[];
}

export const useModelStore = create<ModelStore>((set, get) => ({
  models: [],
  isLoading: false,
  error: null,

  fetchModels: async () => {
    set({ isLoading: true, error: null });
    try {
      const models = await api.listModels() as LLMModel[];
      set({ models, isLoading: false });
    } catch (err: any) {
      set({ error: err.message, isLoading: false });
    }
  },

  addModel: async (modelData) => {
    set({ isLoading: true, error: null });
    try {
      const model = await api.createModel(modelData) as LLMModel;
      set((state) => ({ models: [...state.models, model], isLoading: false }));
      return model;
    } catch (err: any) {
      set({ error: err.message, isLoading: false });
      throw err;
    }
  },

  updateModel: async (id, data) => {
    set({ isLoading: true, error: null });
    try {
      const model = await api.updateModel(id, data) as LLMModel;
      set((state) => ({
        models: state.models.map((m) => (m.id === id ? model : m)),
        isLoading: false,
      }));
    } catch (err: any) {
      set({ error: err.message, isLoading: false });
      throw err;
    }
  },

  deleteModel: async (id) => {
    set({ isLoading: true, error: null });
    try {
      await api.deleteModel(id);
      set((state) => ({
        models: state.models.filter((m) => m.id !== id),
        isLoading: false,
      }));
    } catch (err: any) {
      set({ error: err.message, isLoading: false });
      throw err;
    }
  },

  getModelsByCapability: (capability) => {
    return get().models.filter((m) => m.enabled && m.capabilities.includes(capability));
  },

  getEnabledModels: () => {
    return get().models.filter((m) => m.enabled);
  },
}));
