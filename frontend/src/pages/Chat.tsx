import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import apiClient from '../lib/api';
import type { Chat } from '../types';
import {
  MessageSquare,
  Send,
  Plus,
  Upload,
  Loader2,
  Bot,
  User,
  FileText,
  Trash2,
  Settings,
  LogOut,
  BarChart,
  Sparkles,
} from 'lucide-react';

export default function ChatPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const scrollRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [selectedChatId, setSelectedChatId] = useState<string | null>(null);
  const [messageInput, setMessageInput] = useState('');
  const [uploadingFile, setUploadingFile] = useState(false);

  // Fetch all chats
  const { data: chats, isLoading: chatsLoading } = useQuery<Chat[]>({
    queryKey: ['chats'],
    queryFn: async () => {
      const response = await apiClient.get('/chat/');
      return response.data.chats || [];
    },
  });

  // Fetch selected chat
  const { data: activeChat, isLoading: chatLoading } = useQuery<Chat>({
    queryKey: ['chat', selectedChatId],
    queryFn: async () => {
      const response = await apiClient.get(`/chat/${selectedChatId}`);
      return response.data;
    },
    enabled: !!selectedChatId,
  });

  // Create new chat mutation
  const createChatMutation = useMutation({
    mutationFn: async () => {
      const response = await apiClient.post('/chat/new', { title: 'New Chat' });
      return response.data;
    },
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['chats'] });
      setSelectedChatId(data.chat_id);
    },
  });

  // Send message mutation
  const sendMessageMutation = useMutation({
    mutationFn: async (message: string) => {
      const response = await apiClient.post('/chat/send', {
        chat_id: selectedChatId,
        content: message,
      });
      return response.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['chat', selectedChatId] });
      setMessageInput('');
    },
  });

  // Delete chat mutation
  const deleteChatMutation = useMutation({
    mutationFn: async (chatId: string) => {
      await apiClient.delete(`/chat/${chatId}`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['chats'] });
    },
  });

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [activeChat?.messages]);

  // Select first chat on load
  useEffect(() => {
    if (chats && chats.length > 0 && !selectedChatId) {
      setSelectedChatId(chats[0].chat_id);
    }
  }, [chats, selectedChatId]);

  const handleSendMessage = () => {
    if (!messageInput.trim() || !selectedChatId) return;
    sendMessageMutation.mutate(messageInput);
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !selectedChatId) return;

    setUploadingFile(true);

    try {
      const formData = new FormData();
      formData.append('chat_id', selectedChatId);
      formData.append('file', file);

      await apiClient.post('/upload', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });

      queryClient.invalidateQueries({ queryKey: ['chat', selectedChatId] });
    } catch (err) {
      console.error('File upload failed:', err);
    } finally {
      setUploadingFile(false);
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    }
  };

  const formatTime = (timestamp: string) => {
    return new Date(timestamp).toLocaleTimeString('en-US', {
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  const handleLogout = () => {
    localStorage.removeItem('api_key');
    navigate('/login');
  };

  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column' }}>
      {/* Top Header */}
      <header style={{ background: 'white', borderBottom: '1px solid #d1fae5', padding: '12px 16px', boxShadow: '0 1px 3px rgba(0,0,0,0.1)' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <div style={{ width: '40px', height: '40px', background: 'linear-gradient(135deg, #10b981 0%, #14b8a6 100%)', borderRadius: '12px', display: 'flex', alignItems: 'center', justifyContent: 'center', boxShadow: '0 2px 4px rgba(16, 185, 129, 0.3)' }}>
              <Bot size={24} color="white" strokeWidth={2.5} />
            </div>
            <div>
              <h1 style={{ fontSize: '18px', fontWeight: 'bold', background: 'linear-gradient(135deg, #059669 0%, #14b8a6 100%)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>
                ACMG Assistant
              </h1>
              <p style={{ fontSize: '11px', color: '#6b7280' }}>AI-Powered Variant Analysis</p>
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <button onClick={() => navigate('/dashboard')} style={{ display: 'flex', alignItems: 'center', gap: '6px', padding: '8px 12px', background: 'transparent', border: 'none', borderRadius: '6px', cursor: 'pointer', fontSize: '14px', color: '#374151' }}>
              <BarChart size={16} />
              <span>Dashboard</span>
            </button>
            <button onClick={() => navigate('/settings')} style={{ padding: '8px', background: 'transparent', border: 'none', borderRadius: '6px', cursor: 'pointer', color: '#374151' }}>
              <Settings size={16} />
            </button>
            <button onClick={handleLogout} style={{ padding: '8px', background: 'transparent', border: 'none', borderRadius: '6px', cursor: 'pointer', color: '#374151' }}>
              <LogOut size={16} />
            </button>
          </div>
        </div>
      </header>

      <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>
        {/* Sidebar - Chat List */}
        <div style={{ width: '320px', background: 'white', borderRight: '1px solid #d1fae5', display: 'flex', flexDirection: 'column', boxShadow: '1px 0 3px rgba(0,0,0,0.05)' }}>
          {/* New Chat Button */}
          <div style={{ padding: '16px' }}>
            <button
              onClick={() => createChatMutation.mutate()}
              disabled={createChatMutation.isPending}
              style={{ width: '100%', padding: '12px', background: createChatMutation.isPending ? '#9ca3af' : 'linear-gradient(135deg, #10b981 0%, #14b8a6 100%)', color: 'white', border: 'none', borderRadius: '8px', fontSize: '14px', fontWeight: '600', cursor: createChatMutation.isPending ? 'not-allowed' : 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px', boxShadow: '0 2px 4px rgba(16, 185, 129, 0.3)' }}
            >
              {createChatMutation.isPending ? (
                <>
                  <Loader2 size={16} style={{ animation: 'spin 1s linear infinite' }} />
                  <span>Creating...</span>
                </>
              ) : (
                <>
                  <Plus size={16} />
                  <span>New Conversation</span>
                </>
              )}
            </button>
          </div>

          <div style={{ borderTop: '1px solid #e5e7eb' }} />

          {/* Chat List */}
          <div style={{ flex: 1, overflowY: 'auto', padding: '8px' }}>
            {chatsLoading ? (
              <div style={{ textAlign: 'center', paddingTop: '32px' }}>
                <Loader2 size={24} style={{ margin: '0 auto', color: '#10b981', animation: 'spin 1s linear infinite' }} />
              </div>
            ) : !chats || chats.length === 0 ? (
              <div style={{ textAlign: 'center', padding: '32px 16px' }}>
                <MessageSquare size={48} color="#9ca3af" style={{ margin: '0 auto 8px' }} />
                <p style={{ fontSize: '14px', color: '#6b7280' }}>No conversations yet</p>
                <p style={{ fontSize: '12px', color: '#9ca3af', marginTop: '4px' }}>Start a new chat to begin</p>
              </div>
            ) : (
              chats.map((chat) => (
                <div
                  key={chat.chat_id}
                  onClick={() => setSelectedChatId(chat.chat_id)}
                  style={{
                    padding: '12px',
                    borderRadius: '8px',
                    cursor: 'pointer',
                    marginBottom: '8px',
                    border: selectedChatId === chat.chat_id ? '2px solid #10b981' : '2px solid transparent',
                    background: selectedChatId === chat.chat_id ? '#d1fae5' : 'transparent',
                    transition: 'all 0.2s',
                  }}
                  onMouseEnter={(e) => {
                    if (selectedChatId !== chat.chat_id) {
                      e.currentTarget.style.background = '#f0fdf4';
                    }
                  }}
                  onMouseLeave={(e) => {
                    if (selectedChatId !== chat.chat_id) {
                      e.currentTarget.style.background = 'transparent';
                    }
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'start', justifyContent: 'space-between' }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <p style={{ fontWeight: '500', fontSize: '14px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {chat.title}
                      </p>
                      <p style={{ fontSize: '12px', color: '#6b7280', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', marginTop: '4px' }}>
                        {chat.messages && chat.messages.length > 0 ? chat.messages[chat.messages.length - 1]?.content.slice(0, 40) : 'New chat'}
                      </p>
                    </div>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        deleteChatMutation.mutate(chat.chat_id);
                      }}
                      style={{ padding: '4px', background: 'transparent', border: 'none', cursor: 'pointer', opacity: 0 }}
                      onMouseEnter={(e) => {
                        (e.currentTarget.parentElement?.parentElement as HTMLElement).style.opacity = '1';
                        e.currentTarget.style.opacity = '1';
                      }}
                    >
                      <Trash2 size={14} color="#ef4444" />
                    </button>
                  </div>
                  <p style={{ fontSize: '11px', color: '#9ca3af', marginTop: '8px' }}>
                    {new Date(chat.updated_at).toLocaleDateString()}
                  </p>
                </div>
              ))
            )}
          </div>

          {/* Help Section */}
          <div style={{ padding: '16px', background: '#d1fae5', borderTop: '1px solid #10b981' }}>
            <div style={{ fontSize: '11px' }}>
              <p style={{ fontWeight: '600', color: '#065f46', display: 'flex', alignItems: 'center', gap: '4px', marginBottom: '6px' }}>
                <Sparkles size={12} />
                Quick Commands:
              </p>
              <p style={{ color: '#047857', marginBottom: '2px' }}><code>/analyze</code> - Start variant analysis</p>
              <p style={{ color: '#047857', marginBottom: '2px' }}><code>/help</code> - Get help</p>
              <p style={{ color: '#047857' }}><code>/status</code> - Check analysis status</p>
            </div>
          </div>
        </div>

        {/* Main Chat Area */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
          {!selectedChatId || !activeChat ? (
            <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <div style={{ textAlign: 'center', maxWidth: '600px', padding: '20px' }}>
                <div style={{ width: '80px', height: '80px', background: 'linear-gradient(135deg, #10b981 0%, #14b8a6 100%)', borderRadius: '24px', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 16px', boxShadow: '0 8px 16px rgba(16, 185, 129, 0.3)' }}>
                  <Bot size={40} color="white" strokeWidth={2.5} />
                </div>
                <h2 style={{ fontSize: '24px', fontWeight: 'bold', color: '#1f2937', marginBottom: '8px' }}>
                  Welcome to ACMG Assistant
                </h2>
                <p style={{ color: '#6b7280', marginBottom: '24px', lineHeight: '1.6' }}>
                  Your AI-powered genomics analysis companion. Upload VCF files, ask questions, and get ACMG classifications through natural conversation.
                </p>
                <button
                  onClick={() => createChatMutation.mutate()}
                  style={{ padding: '12px 24px', background: 'linear-gradient(135deg, #10b981 0%, #14b8a6 100%)', color: 'white', border: 'none', borderRadius: '8px', fontSize: '14px', fontWeight: '600', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: '8px', boxShadow: '0 4px 8px rgba(16, 185, 129, 0.3)' }}
                >
                  <Plus size={20} />
                  Start New Conversation
                </button>
              </div>
            </div>
          ) : (
            <>
              {/* Messages Area */}
              <div ref={scrollRef} style={{ flex: 1, overflowY: 'auto', padding: '24px' }}>
                <div style={{ maxWidth: '1000px', margin: '0 auto' }}>
                  {chatLoading ? (
                    <div style={{ textAlign: 'center', paddingTop: '32px' }}>
                      <Loader2 size={32} style={{ margin: '0 auto', color: '#10b981', animation: 'spin 1s linear infinite' }} />
                    </div>
                  ) : !activeChat.messages || activeChat.messages.length === 0 ? (
                    <div style={{ textAlign: 'center', padding: '48px 0' }}>
                      <Bot size={64} color="#9ca3af" style={{ margin: '0 auto 16px' }} />
                      <p style={{ fontSize: '18px', fontWeight: '500', color: '#1f2937', marginBottom: '8px' }}>
                        Ready to assist you!
                      </p>
                      <p style={{ fontSize: '14px', color: '#6b7280' }}>
                        Ask me anything about variant analysis, upload a VCF file, or start a conversation.
                      </p>
                    </div>
                  ) : (
                    activeChat.messages.map((message, idx) => (
                      <div key={idx} style={{ display: 'flex', gap: '12px', marginBottom: '24px', justifyContent: message.role === 'user' ? 'flex-end' : 'flex-start' }}>
                        {message.role === 'assistant' && (
                          <div style={{ width: '32px', height: '32px', borderRadius: '50%', background: 'linear-gradient(135deg, #10b981 0%, #14b8a6 100%)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, boxShadow: '0 2px 4px rgba(16, 185, 129, 0.3)' }}>
                            <Bot size={20} color="white" />
                          </div>
                        )}
                        <div style={{ display: 'flex', flexDirection: 'column', alignItems: message.role === 'user' ? 'flex-end' : 'flex-start', maxWidth: '70%' }}>
                          <div
                            style={{
                              borderRadius: '16px',
                              padding: '12px 16px',
                              background: message.role === 'user' ? 'linear-gradient(135deg, #10b981 0%, #14b8a6 100%)' : 'white',
                              color: message.role === 'user' ? 'white' : '#1f2937',
                              border: message.role === 'assistant' ? '1px solid #d1fae5' : 'none',
                              boxShadow: '0 2px 4px rgba(0,0,0,0.1)',
                            }}
                          >
                            {message.file_info && (
                              <div style={{ marginBottom: '8px', display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px' }}>
                                <FileText size={16} />
                                <span style={{ fontWeight: '500' }}>{message.file_info.filename}</span>
                                <span style={{ padding: '2px 6px', background: '#d1fae5', border: '1px solid #10b981', borderRadius: '4px', fontSize: '11px', color: '#065f46' }}>
                                  {message.file_info.file_type.toUpperCase()}
                                </span>
                              </div>
                            )}
                            <p style={{ fontSize: '14px', whiteSpace: 'pre-wrap', lineHeight: '1.5' }}>{message.content}</p>
                          </div>
                          <span style={{ fontSize: '11px', color: '#9ca3af', marginTop: '4px', padding: '0 4px' }}>
                            {formatTime(message.timestamp)}
                          </span>
                        </div>
                        {message.role === 'user' && (
                          <div style={{ width: '32px', height: '32px', borderRadius: '50%', background: 'linear-gradient(135deg, #a855f7 0%, #ec4899 100%)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                            <User size={20} color="white" />
                          </div>
                        )}
                      </div>
                    ))
                  )}
                  {sendMessageMutation.isPending && (
                    <div style={{ display: 'flex', gap: '12px' }}>
                      <div style={{ width: '32px', height: '32px', borderRadius: '50%', background: 'linear-gradient(135deg, #10b981 0%, #14b8a6 100%)', display: 'flex', alignItems: 'center', justifyContent: 'center', boxShadow: '0 2px 4px rgba(16, 185, 129, 0.3)' }}>
                        <Bot size={20} color="white" />
                      </div>
                      <div style={{ background: 'white', border: '1px solid #d1fae5', borderRadius: '16px', padding: '12px 16px', boxShadow: '0 2px 4px rgba(0,0,0,0.1)' }}>
                        <Loader2 size={20} color="#10b981" style={{ animation: 'spin 1s linear infinite' }} />
                      </div>
                    </div>
                  )}
                </div>
              </div>

              {/* Input Area */}
              <div style={{ borderTop: '1px solid #d1fae5', background: 'white', padding: '16px', boxShadow: '0 -1px 3px rgba(0,0,0,0.05)' }}>
                <div style={{ maxWidth: '1000px', margin: '0 auto' }}>
                  {uploadingFile && (
                    <div style={{ marginBottom: '12px', padding: '12px', background: '#d1fae5', border: '1px solid #10b981', borderRadius: '8px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                      <Loader2 size={16} color="#10b981" style={{ animation: 'spin 1s linear infinite' }} />
                      <span style={{ fontSize: '14px', color: '#065f46' }}>Uploading and analyzing file...</span>
                    </div>
                  )}
                  <div style={{ display: 'flex', gap: '8px' }}>
                    <input
                      ref={fileInputRef}
                      type="file"
                      accept=".vcf,.vcf.gz,.pdf,.csv,.txt"
                      onChange={handleFileUpload}
                      style={{ display: 'none' }}
                    />
                    <button
                      onClick={() => fileInputRef.current?.click()}
                      disabled={uploadingFile || !selectedChatId}
                      style={{ padding: '12px', background: 'white', border: '1px solid #d1d5db', borderRadius: '8px', cursor: uploadingFile || !selectedChatId ? 'not-allowed' : 'pointer', flexShrink: 0 }}
                    >
                      <Upload size={20} color="#6b7280" />
                    </button>
                    <textarea
                      placeholder="Ask about variants, upload VCF files, or type /help for commands..."
                      value={messageInput}
                      onChange={(e) => setMessageInput(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' && !e.shiftKey) {
                          e.preventDefault();
                          handleSendMessage();
                        }
                      }}
                      disabled={sendMessageMutation.isPending}
                      style={{ flex: 1, padding: '12px', border: '1px solid #d1d5db', borderRadius: '8px', fontSize: '14px', resize: 'none', minHeight: '60px', maxHeight: '200px', fontFamily: 'inherit' }}
                      rows={2}
                    />
                    <button
                      onClick={handleSendMessage}
                      disabled={!messageInput.trim() || sendMessageMutation.isPending}
                      style={{ padding: '12px', background: !messageInput.trim() || sendMessageMutation.isPending ? '#9ca3af' : 'linear-gradient(135deg, #10b981 0%, #14b8a6 100%)', color: 'white', border: 'none', borderRadius: '8px', cursor: !messageInput.trim() || sendMessageMutation.isPending ? 'not-allowed' : 'pointer', flexShrink: 0, boxShadow: '0 2px 4px rgba(16, 185, 129, 0.3)' }}
                    >
                      <Send size={20} />
                    </button>
                  </div>
                  <p style={{ fontSize: '11px', color: '#9ca3af', marginTop: '8px', textAlign: 'center' }}>
                    Press Enter to send • Shift+Enter for new line • Upload VCF, PDF, CSV, or TXT files
                  </p>
                </div>
              </div>
            </>
          )}
        </div>
      </div>

      <style>{`
        @keyframes spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
}
