import axios from 'axios';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
  timeout: 300000, // 5 minutes timeout for large file uploads
  maxContentLength: 100 * 1024 * 1024, // 100MB max content length
  maxBodyLength: 100 * 1024 * 1024, // 100MB max body length
});

apiClient.interceptors.request.use((config) => {
  const apiKey = localStorage.getItem('api_key');
  if (apiKey) {
    config.headers['X-API-Key'] = apiKey;
  }
  return config;
});

export default apiClient;

export const getApiKey = () => localStorage.getItem('api_key');
export const setApiKey = (key: string) => localStorage.setItem('api_key', key);
export const clearApiKey = () => localStorage.removeItem('api_key');
