import React, { useState, useEffect, useRef } from 'react';
import { 
  Database, 
  RefreshCw, 
  Send, 
  AlertTriangle, 
  Moon, 
  Sun, 
  ChevronDown, 
  ChevronRight,
  Sparkles,
  Link2Off,
  HelpCircle
} from 'lucide-react';
import './App.css';

const API_BASE = 'http://127.0.0.1:8000';


interface TableMeta {
  comment: string;
  columns: Record<string, string>;
}

interface SchemaData {
  schema: string;
  tables: Record<string, TableMeta>;
}

interface ClarificationOption {
  column: string;
  comment: string;
  score: number;
}

interface Clarification {
  phrase: string;
  reason: string;
  options: ClarificationOption[];
}

interface Message {
  id: string;
  type: 'user' | 'bot';
  text: string;
  sql?: string;
  preview?: string;
  tables?: string[];
  warnings?: string[];
  clarifications?: Clarification[];
  executed?: boolean;
  rows?: any[][];
  columns?: string[];
  error?: string;
  loading?: boolean;
}

export default function App() {
  const [theme, setTheme] = useState<'light' | 'dark'>('light');
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  
  // Connection and Metadata states
  const [dbStatus, setDbStatus] = useState<'online' | 'offline'>('offline');
  const [schemaMeta, setSchemaMeta] = useState<SchemaData | null>(null);
  const [showMeta, setShowMeta] = useState(false);
  const [expandedTables, setExpandedTables] = useState<Record<string, boolean>>({});
  const [metaLoading, setMetaLoading] = useState(false);
  
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Check DB status on mount and fetch schema
  useEffect(() => {
    checkHealth();
    loadMetadata();
    
    // Default theme based on system preference
    if (window.matchMedia('(prefers-color-scheme: dark)').matches) {
      setTheme('dark');
      document.documentElement.classList.add('dark');
    }
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const toggleTheme = () => {
    if (theme === 'light') {
      setTheme('dark');
      document.documentElement.classList.add('dark');
    } else {
      setTheme('light');
      document.documentElement.classList.remove('dark');
    }
  };

  const checkHealth = async () => {
    try {
      const res = await fetch(`${API_BASE}/health`);
      if (res.ok) {
        setDbStatus('online');
      } else {
        setDbStatus('offline');
      }
    } catch {
      setDbStatus('offline');
    }
  };

  const loadMetadata = async () => {
    setMetaLoading(true);
    try {
      const res = await fetch(`${API_BASE}/schema`);
      if (res.ok) {
        const data = await res.json();
        setSchemaMeta(data);
        // Expand first table by default if available
        if (data.tables) {
          const firstTable = Object.keys(data.tables)[0];
          if (firstTable) {
            setExpandedTables({ [firstTable]: true });
          }
        }
      }
    } catch (e) {
      console.error("Failed to load schema metadata:", e);
    } finally {
      setMetaLoading(false);
    }
  };

  const toggleTableExpand = (table: string) => {
    setExpandedTables(prev => ({
      ...prev,
      [table]: !prev[table]
    }));
  };

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  const startNewChat = () => {
    setMessages([]);
    setInput('');
    if (inputRef.current) {
      inputRef.current.focus();
    }
  };

  const submitQuery = async (queryText: string) => {
    const trimmed = queryText.trim();
    if (!trimmed) return;

    // Add user message
    const userMsgId = Date.now().toString();
    const newMsg: Message = {
      id: userMsgId,
      type: 'user',
      text: trimmed
    };
    
    // Add bot typing placeholder
    const botMsgId = (Date.now() + 1).toString();
    const botPlaceholder: Message = {
      id: botMsgId,
      type: 'bot',
      text: '',
      loading: true
    };

    setMessages(prev => [...prev, newMsg, botPlaceholder]);
    setInput('');
    setLoading(true);

    try {
      // Use /run to translate and execute in one step
      const response = await fetch(`${API_BASE}/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: trimmed })
      });

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      const data = await response.json();
      
      // Update bot message with actual results
      setMessages(prev => prev.map(msg => {
        if (msg.id === botMsgId) {
          return {
            id: botMsgId,
            type: 'bot',
            text: data.ok 
              ? `I translated your request into a SQL statement.` 
              : `I was unable to construct a query for that request.`,
            sql: data.sql,
            preview: data.preview,
            tables: data.tables,
            warnings: data.warnings,
            clarifications: data.clarifications,
            executed: data.executed,
            rows: data.rows,
            columns: data.columns,
            error: data.error,
            loading: false
          };
        }
        return msg;
      }));

    } catch (err: any) {
      setMessages(prev => prev.map(msg => {
        if (msg.id === botMsgId) {
          return {
            id: botMsgId,
            type: 'bot',
            text: 'An error occurred while connecting to the NL2SQL backend server.',
            error: err.message,
            loading: false
          };
        }
        return msg;
      }));
    } finally {
      setLoading(false);
      // Check database status again
      checkHealth();
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submitQuery(input);
    }
  };

  const selectClarificationOption = (phrase: string, optionColumn: string) => {
    // Resolve clarification by rewriting input or sending direct target
    const promptText = `For "${phrase}", use column "${optionColumn}"`;
    submitQuery(promptText);
  };

  return (
    <div className="app-container">
      {/* Sidebar */}
      <aside className="sidebar">
        <div className="sidebar-header">
          <div className="logo-info">
            <h1 className="logo-title">askdb</h1>
            <p className="logo-subtitle">ask your database</p>
          </div>
          <button 
            className="theme-toggle-btn" 
            onClick={toggleTheme}
            title={`Switch to ${theme === 'light' ? 'Dark' : 'Light'} Mode`}
          >
            {theme === 'light' ? <Moon size={16} /> : <Sun size={16} />}
          </button>
        </div>

        <button className="new-chat-btn" onClick={startNewChat}>
          <Sparkles size={15} />
          New chat
        </button>

        {/* METADATA Section */}
        <div className="sidebar-section">
          <h2 className="section-title">Metadata</h2>
          
          <button 
            className="sidebar-btn" 
            onClick={loadMetadata} 
            disabled={metaLoading}
          >
            <RefreshCw size={14} className={metaLoading ? 'spinner' : ''} />
            {schemaMeta ? 'Reload metadata' : 'Load metadata'}
          </button>

          <button 
            className={`sidebar-btn ${showMeta ? 'active' : ''}`}
            onClick={() => setShowMeta(!showMeta)}
            disabled={!schemaMeta}
          >
            <Database size={14} />
            Show metadata
          </button>

          {showMeta && schemaMeta && (
            <div className="metadata-catalog">
              {Object.entries(schemaMeta.tables).map(([tableName, tableInfo]) => (
                <div className="meta-table-item" key={tableName}>
                  <div 
                    className="meta-table-header"
                    onClick={() => toggleTableExpand(tableName)}
                  >
                    {expandedTables[tableName] ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                    <span>{tableName}</span>
                  </div>
                  {tableInfo.comment && (
                    <div className="meta-table-comment">{tableInfo.comment}</div>
                  )}
                  {expandedTables[tableName] && (
                    <div className="meta-columns-list">
                      {Object.entries(tableInfo.columns).map(([colName, colDesc]) => (
                        <div className="meta-col-item" key={colName}>
                          <span className="meta-col-name">{colName}</span>
                          {colDesc && <span className="meta-col-desc">{colDesc}</span>}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* CONNECTION Section */}
        <div className="sidebar-footer">
          <div className="sidebar-section" style={{ marginBottom: 0 }}>
            <h2 className="section-title">Connection</h2>
            <div className="conn-status">
              <span className={`status-dot ${dbStatus === 'online' ? 'online' : 'offline'}`}></span>
              <span>{dbStatus === 'online' ? 'local' : 'offline'}</span>
            </div>
            {dbStatus === 'online' && (
              <button className="disconnect-btn" onClick={() => setDbStatus('offline')}>
                Disconnect
              </button>
            )}
          </div>
        </div>
      </aside>

      {/* Main Chat Area */}
      <main className="chat-area">
        {messages.length === 0 ? (
          <div className="welcome-container">
            <h1 className="welcome-title">Ask your database</h1>
            <p className="welcome-subtitle">
              Try <span className="suggestion-pill" onClick={() => setInput('list top 10 rows from SECURITIES')}>"list top 10 rows from SECURITIES"</span>
            </p>
            <div className="welcome-tips">
              Tip: load metadata from the sidebar for smarter suggestions.
            </div>
          </div>
        ) : (
          <div className="messages-container">
            {messages.map((msg) => (
              <div className={`message-row ${msg.type}`} key={msg.id}>
                <div className={`avatar-box ${msg.type}`}>
                  {msg.type === 'user' ? 'U' : 'AI'}
                </div>
                <div className="message-content">
                  {msg.loading ? (
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                      <span className="spinner"></span>
                      <span style={{ color: 'var(--text-muted)' }}>Translating and executing query...</span>
                    </div>
                  ) : (
                    <>
                      <p className="message-text">{msg.text}</p>
                      
                      {/* SQL Code Block */}
                      {msg.preview && (
                        <div className="sql-code-container">
                          <div className="sql-code-header">
                            <span>Generated SQL</span>
                            <span style={{ fontFamily: 'var(--font-sans)', fontSize: '10px' }}>
                              {msg.executed ? '✓ Executed' : '⚡ Preview'}
                            </span>
                          </div>
                          <code className="sql-code">
                            {formatSQL(msg.preview)}
                          </code>
                        </div>
                      )}

                      {/* Warnings Block */}
                      {msg.warnings && msg.warnings.map((warn, i) => (
                        <div className="warning-box" key={i}>
                          <AlertTriangle size={15} />
                          <span>{warn}</span>
                        </div>
                      ))}

                      {/* Clarifications Block */}
                      {msg.clarifications && msg.clarifications.map((cl, idx) => (
                        <div className="clarification-card" key={idx}>
                          <div className="clarification-title">
                            <HelpCircle size={15} style={{ color: 'var(--accent-color)' }} />
                            <span>Clarification needed for: <strong>"{cl.phrase}"</strong> ({cl.reason})</span>
                          </div>
                          <div className="clarification-options">
                            {cl.options.map((opt, oIdx) => (
                              <button 
                                className="clarification-option-btn"
                                key={oIdx}
                                onClick={() => selectClarificationOption(cl.phrase, opt.column)}
                              >
                                <span className="clarification-opt-col">{opt.column}</span>
                                {opt.comment && <span className="clarification-opt-desc">{opt.comment}</span>}
                              </button>
                            ))}
                          </div>
                        </div>
                      ))}

                      {/* DB Results Table */}
                      {msg.executed && msg.columns && msg.rows && (
                        <div className="results-table-wrapper">
                          <div className="results-table-header">
                            <span>Database Results</span>
                            <span>{msg.rows.length} rows returned</span>
                          </div>
                          <div className="table-scroll-container">
                            <table className="results-table">
                              <thead>
                                <tr>
                                  {msg.columns.map((col, idx) => (
                                    <th key={idx}>{col}</th>
                                  ))}
                                </tr>
                              </thead>
                              <tbody>
                                {msg.rows.length === 0 ? (
                                  <tr>
                                    <td colSpan={msg.columns.length} style={{ textAlign: 'center', color: 'var(--text-muted)' }}>
                                      No rows returned
                                    </td>
                                  </tr>
                                ) : (
                                  msg.rows.map((row, rIdx) => (
                                    <tr key={rIdx}>
                                      {row.map((cell, cIdx) => (
                                        <td key={cIdx}>{cell !== null ? String(cell) : 'NULL'}</td>
                                      ))}
                                    </tr>
                                  ))
                                )}
                              </tbody>
                            </table>
                          </div>
                        </div>
                      )}

                      {/* Backend/Execution Error Block */}
                      {msg.error && (
                        <div className="warning-box" style={{ backgroundColor: 'rgba(239, 68, 68, 0.08)', border: '1px dashed rgba(239, 68, 68, 0.3)', color: '#ef4444' }}>
                          <Link2Off size={15} />
                          <span>{msg.error}</span>
                        </div>
                      )}
                    </>
                  )}
                </div>
              </div>
            ))}
            <div ref={messagesEndRef} />
          </div>
        )}

        {/* Input Bar */}
        <div className="input-container">
          <div className="input-box">
            <textarea
              className="query-textarea"
              ref={inputRef}
              rows={1}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Ask your database..."
              disabled={loading}
            />
            <button 
              className="send-query-btn"
              onClick={() => submitQuery(input)}
              disabled={loading || !input.trim()}
            >
              <Send size={14} />
            </button>
          </div>
        </div>
      </main>
    </div>
  );
}

// Simple SQL formatter helper
function formatSQL(sql: string): React.ReactNode[] {
  const keywords = [
    'SELECT', 'FROM', 'JOIN', 'ON', 'WHERE', 'AND', 'OR', 'GROUP BY', 
    'ORDER BY', 'FETCH FIRST', 'ROWS ONLY', 'LIMIT', 'AVG', 'SUM', 'COUNT', 'MAX', 'MIN'
  ];
  
  // Split query on words while keeping spaces
  const parts = sql.split(/(\s+)/);
  
  return parts.map((part, i) => {
    const upper = part.toUpperCase();
    if (keywords.includes(upper)) {
      return <span key={i} className="sql-code-keyword">{part}</span>;
    }
    return <span key={i}>{part}</span>;
  });
}
