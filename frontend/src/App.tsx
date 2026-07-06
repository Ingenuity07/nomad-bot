import React, { useState, useRef, useEffect } from 'react';
import axios from 'axios';
import { Bot, Send } from 'lucide-react';
import './index.css';

interface Message {
  id: string;
  role: 'user' | 'ai';
  content: string;
}

const formatContent = (content: string) => {
  return content
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/\n/g, '<br/>');
};

function App() {
  const [messages, setMessages] = useState<Message[]>([
    { id: '1', role: 'ai', content: 'Hello! I am your AIOS frontend. I am currently running on React + Vite.' }
  ]);
  const [input, setInput] = useState('');
  const [isTyping, setIsTyping] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  
  const historyRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (historyRef.current) {
      historyRef.current.scrollTop = historyRef.current.scrollHeight;
    }
  }, [messages, isTyping]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || isTyping) return;

    const userMsg: Message = { id: Date.now().toString(), role: 'user', content: input.trim() };
    setMessages(prev => [...prev, userMsg]);
    setInput('');
    setIsTyping(true);

    try {
      const res = await axios.post('http://localhost:8000/api/chat/', {
        message: userMsg.content,
        conversation_id: conversationId
      });

      setConversationId(res.data.conversation_id);
      
      const aiMsg: Message = { 
        id: (Date.now() + 1).toString(), 
        role: 'ai', 
        content: res.data.response 
      };
      setMessages(prev => [...prev, aiMsg]);
    } catch (err) {
      console.error(err);
      setMessages(prev => [...prev, { 
        id: (Date.now() + 1).toString(), 
        role: 'ai', 
        content: "Sorry, I encountered an error connecting to the backend. Ensure Django is running on port 8000." 
      }]);
    } finally {
      setIsTyping(false);
    }
  };

  return (
    <div className="chat-container">
      <header>
        <Bot size={32} color="#60a5fa" />
        <div>
          <h1>AIOS Frontend</h1>
          <p>React + Vite + Django + Gemini</p>
        </div>
      </header>

      <div className="chat-history" ref={historyRef}>
        {messages.map(msg => (
          <div key={msg.id} className={`message ${msg.role}`}>
            <p dangerouslySetInnerHTML={{ __html: formatContent(msg.content) }} />
          </div>
        ))}
        
        {isTyping && (
          <div className="typing-indicator">
            <span></span><span></span><span></span>
          </div>
        )}
      </div>

      <div className="input-area">
        <form onSubmit={handleSubmit}>
          <input 
            type="text" 
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask me anything..." 
            autoComplete="off"
            disabled={isTyping}
          />
          <button type="submit" disabled={isTyping || !input.trim()}>
            <Send size={20} />
          </button>
        </form>
      </div>
    </div>
  );
}

export default App;
