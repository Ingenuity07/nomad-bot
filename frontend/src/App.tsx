import React, { useState, useRef, useEffect } from 'react';
import axios from 'axios';
import { Send, Globe, Activity, AlertTriangle, ChevronUp, ChevronDown, Cpu, X, Zap, Layers, CheckCircle2, XCircle, Database, FileText, Sliders, BarChart3, Sparkles, Code, Check } from 'lucide-react';
import './index.css';

interface Message {
  id: string;
  role: 'user' | 'ai' | 'assistant' | 'system' | 'tool';
  content: string;
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
  provider?: string;
  model?: string;
}

interface ProviderInfo {
  key: string;
  model_name: string;
  has_key: boolean;
  is_healthy: boolean;
  status: 'healthy' | 'missing_key' | 'cooldown';
}

interface ProvidersStatusData {
  providers: ProviderInfo[];
  fallbacks: Record<string, string[]>;
}

interface ToolLog {
  id: string;
  name: string;
  args: any;
  result: string;
  status: 'success' | 'error';
}

type AgentType = 'general' | 'job_search' | 'resume_tailor' | 'browser_headless';
type MainTab = 'chat' | 'knowledge_base' | 'tailor' | 'versions' | 'prospecting';

function App() {
  const [messages, setMessages] = useState<Message[]>([
    { id: '1', role: 'ai', content: 'Hello! Welcome to Nomad V3: Personal Career Operating System. Use the top navigation bar to manage your Professional Knowledge Base, run ATS Gap Analysis, compile deterministic LaTeX resumes, and track applications!' }
  ]);
  const [input, setInput] = useState('');
  const [isTyping, setIsTyping] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [activeModel, setActiveModel] = useState<string | null>(null);
  const [activeProvider, setActiveProvider] = useState<string | null>(null);
  
  // Nomad V3 Navigation & State
  const [activeTab, setActiveTab] = useState<MainTab>('chat');
  const [kbData, setKbData] = useState<any>(null);
  const [ingestText, setIngestText] = useState('');
  const [isIngesting, setIsIngesting] = useState(false);
  
  // Job Tailor V3 state
  const [tailorJobText, setTailorJobText] = useState('');
  const [isTailoring, setIsTailoring] = useState(false);
  const [tailoredResult, setTailoredResult] = useState<any>(null);
  const [activeLatexView, setActiveLatexView] = useState<'preview' | 'latex'>('preview');

  // Resume Template States
  const [templates, setTemplates] = useState<any[]>([]);
  const [selectedTemplateName, setSelectedTemplateName] = useState('modern');
  const [newTemplateName, setNewTemplateName] = useState('');
  const [newTemplateSource, setNewTemplateSource] = useState('');
  const [newTemplateFile, setNewTemplateFile] = useState<File | null>(null);
  const [isUploadingTemplate, setIsUploadingTemplate] = useState(false);
  const [showUploadTemplateCard, setShowUploadTemplateCard] = useState(false);

  // Versions & Applications V3 state
  const [resumeVersions, setResumeVersions] = useState<any[]>([]);
  const [applications, setApplications] = useState<any[]>([]);

  // Lead Generation Prospecting Engine States
  const [prospectKeyword, setProspectKeyword] = useState('');
  const [prospectLocation, setProspectLocation] = useState('');
  const [prospectLeads, setProspectLeads] = useState<any[]>([]);
  const [isProspecting, setIsProspecting] = useState(false);
  const [selectedLead, setSelectedLead] = useState<any>(null);

  // Filter & Pagination States
  const [prospectFilterScore, setProspectFilterScore] = useState('');
  const [prospectFilterLocation, setProspectFilterLocation] = useState('');
  const [debouncedFilterLocation, setDebouncedFilterLocation] = useState('');
  const [prospectFilterCategory, setProspectFilterCategory] = useState('');
  const [prospectPage, setProspectPage] = useState(1);
  const [prospectTotalPages, setProspectTotalPages] = useState(1);
  const [prospectTotalCount, setProspectTotalCount] = useState(0);
  const [prospectCategoriesList, setProspectCategoriesList] = useState<string[]>([]);
  
  // User Model Selection State (Default: auto)
  const [userSelectedProvider, setUserSelectedProvider] = useState<string>('auto');
  
  // Models modal state
  const [showModelsModal, setShowModelsModal] = useState(false);
  const [providersData, setProvidersData] = useState<ProvidersStatusData | null>(null);

  // Debounce Location input changes by 400ms to avoid overlapping API requests
  useEffect(() => {
    const handler = setTimeout(() => {
      setDebouncedFilterLocation(prospectFilterLocation);
    }, 400);
    return () => clearTimeout(handler);
  }, [prospectFilterLocation]);

  // Agent selector & field states
  const [agentType, setAgentType] = useState<AgentType>('general');
  const [jobTitle, setJobTitle] = useState('');
  const [jobLocation, setJobLocation] = useState('');
  const [jobKeywords, setJobKeywords] = useState('');
  const [jobCompany, setJobCompany] = useState('');
  const [resumeText, setResumeText] = useState('');
  const [jobDescription, setJobDescription] = useState('');
  const [browserUrl, setBrowserUrl] = useState('');
  const [browserCommand, setBrowserCommand] = useState('');
  const [showParams, setShowParams] = useState(true);

  // Execution states for Visualizer
  const [activeNode, setActiveNode] = useState<string | null>(null);
  const [planSteps, setPlanSteps] = useState<{ name: string; status: 'pending' | 'active' | 'completed' }[]>([]);
  const [toolLogs, setToolLogs] = useState<ToolLog[]>([]);
  const [critiqueMsg, setCritiqueMsg] = useState<string | null>(null);
  const [retryCount, setRetryCount] = useState(0);
  const [approvalMessage, setApprovalMessage] = useState<string | null>(null);
  const [systemLogs, setSystemLogs] = useState<string[]>([]);

  // Conversation History states
  const [conversations, setConversations] = useState<{ id: string; title: string; created_at: string }[]>([]);

  const fetchConversations = async () => {
    try {
      const res = await axios.get('http://localhost:8000/api/conversations/');
      setConversations(res.data);
    } catch (err) {
      console.error("Failed to load conversations:", err);
    }
  };

  const fetchKnowledgeBase = async () => {
    try {
      const res = await axios.get('http://localhost:8000/api/v3/knowledge-base/');
      setKbData(res.data);
    } catch (err) {
      console.error("Failed to load Knowledge Base:", err);
    }
  };

  const [ingestFile, setIngestFile] = useState<File | null>(null);

  // Profile Enrichment States
  const [enrichExperience, setEnrichExperience] = useState({ company: '', role: '', start_date: '', end_date: '', bullet_points: '', tech_stack: '' });
  const [enrichProject, setEnrichProject] = useState({ title: '', description: '', tech_stack: '', impact_metrics: '' });
  const [enrichSkill, setEnrichSkill] = useState({ category: 'Languages', name: '', proficiency: 'Intermediate' });

  const handleIngestResume = async (fileToIngest?: File) => {
    const targetFile = fileToIngest || ingestFile;
    if (!targetFile && !ingestText.trim()) return;
    setIsIngesting(true);
    try {
      if (targetFile) {
        const formData = new FormData();
        formData.append("file", targetFile);
        await axios.post('http://localhost:8000/api/v3/knowledge-base/ingest/', formData, {
          headers: {
            'Content-Type': 'multipart/form-data'
          }
        });
        setIngestFile(null);
      } else {
        await axios.post('http://localhost:8000/api/v3/knowledge-base/ingest/', {
          resume_text: ingestText
        });
        setIngestText('');
      }
      fetchKnowledgeBase();
      alert("Resume parsed and ingested successfully!");
    } catch (err) {
      console.error("Ingestion error:", err);
      alert("Failed to ingest resume.");
    } finally {
      setIsIngesting(false);
    }
  };

  const handleResetProfile = async () => {
    if (!confirm("Are you sure you want to reset your career profile? This will clear all experiences, projects, skills, versions, and application logs.")) return;
    try {
      await axios.post('http://localhost:8000/api/v3/knowledge-base/reset/');
      fetchKnowledgeBase();
      fetchVersions();
      fetchApplications();
      setTailoredResult(null);
      alert("Career profile has been successfully reset!");
    } catch (err) {
      console.error("Reset error:", err);
      alert("Failed to reset profile.");
    }
  };

  const handleEnrichExperience = async () => {
    if (!enrichExperience.company || !enrichExperience.role) {
      alert("Company and Role are required");
      return;
    }
    try {
      await axios.post('http://localhost:8000/api/v3/knowledge-base/enrich/', {
        experience: {
          ...enrichExperience,
          bullet_points: enrichExperience.bullet_points.split('\n').map(x => x.trim()).filter(x => x),
          tech_stack: enrichExperience.tech_stack.split(',').map(x => x.trim()).filter(x => x)
        }
      });
      setEnrichExperience({ company: '', role: '', start_date: '', end_date: '', bullet_points: '', tech_stack: '' });
      fetchKnowledgeBase();
      alert("Experience added successfully!");
    } catch (err) {
      console.error(err);
      alert("Failed to add experience.");
    }
  };

  const handleEnrichProject = async () => {
    if (!enrichProject.title) {
      alert("Project Title is required");
      return;
    }
    try {
      await axios.post('http://localhost:8000/api/v3/knowledge-base/enrich/', {
        project: {
          ...enrichProject,
          tech_stack: enrichProject.tech_stack.split(',').map(x => x.trim()).filter(x => x),
          impact_metrics: enrichProject.impact_metrics.split('\n').map(x => x.trim()).filter(x => x)
        }
      });
      setEnrichProject({ title: '', description: '', tech_stack: '', impact_metrics: '' });
      fetchKnowledgeBase();
      alert("Project added successfully!");
    } catch (err) {
      console.error(err);
      alert("Failed to add project.");
    }
  };

  const handleEnrichSkill = async () => {
    if (!enrichSkill.name) {
      alert("Skill Name is required");
      return;
    }
    try {
      await axios.post('http://localhost:8000/api/v3/knowledge-base/enrich/', {
        skill: enrichSkill
      });
      setEnrichSkill({ category: 'Languages', name: '', proficiency: 'Intermediate' });
      fetchKnowledgeBase();
      alert("Skill added successfully!");
    } catch (err) {
      console.error(err);
      alert("Failed to add skill.");
    }
  };

  const fetchTemplates = async () => {
    try {
      const res = await axios.get('http://localhost:8000/api/v3/resumes/templates/');
      setTemplates(res.data);
    } catch (err) {
      console.error("Failed to load templates:", err);
    }
  };

  const handleUploadTemplate = async () => {
    if (!newTemplateName.trim()) {
      alert("Template Name is required");
      return;
    }
    if (!newTemplateSource.trim() && !newTemplateFile) {
      alert("Please provide raw LaTeX source or upload a file (.tex or .pdf)");
      return;
    }

    setIsUploadingTemplate(true);
    try {
      const formData = new FormData();
      formData.append('name', newTemplateName);
      if (newTemplateFile) {
        formData.append('file', newTemplateFile);
      } else {
        formData.append('latex_source', newTemplateSource);
      }

      const res = await axios.post('http://localhost:8000/api/v3/resumes/templates/', formData, {
        headers: {
          'Content-Type': 'multipart/form-data'
        }
      });
      alert(`Template "${res.data.name}" added successfully!`);
      setNewTemplateName('');
      setNewTemplateSource('');
      setNewTemplateFile(null);
      fetchTemplates();
      setSelectedTemplateName(res.data.name);
      setShowUploadTemplateCard(false);
    } catch (err: any) {
      console.error("Template upload error:", err);
      const errMsg = err.response?.data?.error || "Failed to upload template.";
      alert(errMsg);
    } finally {
      setIsUploadingTemplate(false);
    }
  };

  const handleTailorResume = async () => {
    if (!tailorJobText.trim()) return;
    setIsTailoring(true);
    try {
      const res = await axios.post('http://localhost:8000/api/v3/resumes/tailor/', {
        job_text: tailorJobText,
        template_name: selectedTemplateName
      });
      setTailoredResult(res.data);
      fetchVersions();
      fetchApplications();
    } catch (err) {
      console.error("Tailoring error:", err);
      alert("Failed to generate tailored resume.");
    } finally {
      setIsTailoring(false);
    }
  };

  const fetchVersions = async () => {
    try {
      const res = await axios.get('http://localhost:8000/api/v3/resumes/versions/');
      setResumeVersions(res.data);
    } catch (err) {
      console.error("Failed to load resume versions:", err);
    }
  };

  const fetchApplications = async () => {
    try {
      const res = await axios.get('http://localhost:8000/api/v3/applications/');
      setApplications(res.data);
    } catch (err) {
      console.error("Failed to load applications:", err);
    }
  };

  const fetchProspectLeads = async (pageOverride?: number) => {
    try {
      const pageToFetch = pageOverride !== undefined ? pageOverride : prospectPage;
      const params = new URLSearchParams();
      params.append('page', String(pageToFetch));
      params.append('page_size', '10');
      if (prospectFilterScore) params.append('score_min', prospectFilterScore);
      if (debouncedFilterLocation) params.append('location', debouncedFilterLocation);
      if (prospectFilterCategory) params.append('category', prospectFilterCategory);

      const res = await axios.get(`http://localhost:8000/api/v3/prospecting/leads/?${params.toString()}`);
      setProspectLeads(res.data.leads || []);
      setProspectTotalPages(res.data.total_pages || 1);
      setProspectTotalCount(res.data.total_count || 0);
      setProspectCategoriesList(res.data.categories || []);
      if (pageOverride !== undefined) {
        setProspectPage(pageOverride);
      }
    } catch (err) {
      console.error("Failed to load prospect leads:", err);
    }
  };

  const handleRunProspecting = async () => {
    if (!prospectKeyword.trim() || !prospectLocation.trim()) {
      alert("Keyword and Location are required");
      return;
    }
    setIsProspecting(true);
    try {
      await axios.post('http://localhost:8000/api/v3/prospecting/discover/', {
        keyword: prospectKeyword,
        location: prospectLocation
      });
      fetchProspectLeads(1);
      alert("Lead discovery run completed successfully!");
    } catch (err) {
      console.error("Prospecting run error:", err);
      alert("Failed to run lead discovery.");
    } finally {
      setIsProspecting(false);
    }
  };

  const handleResetProspecting = async () => {
    if (!confirm("Are you sure you want to clear all leads in CRM?")) return;
    try {
      await axios.post('http://localhost:8000/api/v3/prospecting/reset/');
      fetchProspectLeads(1);
      setSelectedLead(null);
      alert("Leads directory cleared.");
    } catch (err) {
      console.error(err);
    }
  };

  // Run lead search on filter changes
  useEffect(() => {
    fetchProspectLeads(1);
  }, [prospectFilterScore, debouncedFilterLocation, prospectFilterCategory]);

  useEffect(() => {
    fetchConversations();
    fetchKnowledgeBase();
    fetchVersions();
    fetchApplications();
    fetchTemplates();
  }, []);

  const handleSelectConversation = async (id: string) => {
    if (socketRef.current) {
      socketRef.current.close();
      socketRef.current = null;
    }
    setConversationId(id);
    setToolLogs([]);
    setSystemLogs([]);
    setPlanSteps([]);
    setCritiqueMsg(null);
    setRetryCount(0);
    setApprovalMessage(null);
    setActiveNode(null);

    try {
      const res = await axios.get(`http://localhost:8000/api/conversations/${id}/`);
      if (res.data.selected_model) {
        setActiveModel(res.data.selected_model);
        setActiveProvider(res.data.selected_provider);
      } else {
        setActiveModel(null);
        setActiveProvider(null);
      }
      const msgs = res.data.messages.map((m: any) => ({
        id: m.id,
        role: m.role,
        content: m.content,
        prompt_tokens: m.prompt_tokens,
        completion_tokens: m.completion_tokens,
        total_tokens: m.total_tokens,
        provider: m.provider,
        model: m.model
      }));
      setMessages(msgs.length > 0 ? msgs : [
        { id: '1', role: 'ai', content: 'Conversation is empty.' }
      ]);
      connectWebSocket(id);
    } catch (err) {
      console.error("Failed to load conversation messages:", err);
    }
  };

  const fetchProvidersStatus = async () => {
    try {
      const res = await axios.get('http://localhost:8000/api/providers/');
      setProvidersData(res.data);
      setShowModelsModal(true);
    } catch (err) {
      console.error("Failed to load providers status:", err);
    }
  };

  const handleNewChat = () => {
    if (socketRef.current) {
      socketRef.current.close();
      socketRef.current = null;
    }
    setConversationId(null);
    setActiveModel(null);
    setActiveProvider(null);
    setMessages([
      { id: '1', role: 'ai', content: 'Hello! I am your V2 Agentic Workspace. Select an Agent type below, fill in the custom parameters, and watch the execution visualizer execute your instructions in real-time!' }
    ]);
    setToolLogs([]);
    setSystemLogs([]);
    setPlanSteps([]);
    setCritiqueMsg(null);
    setRetryCount(0);
    setApprovalMessage(null);
    setActiveNode(null);
  };

  const chatHistoryRef = useRef<HTMLDivElement>(null);
  const logsEndRef = useRef<HTMLDivElement>(null);
  const socketRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (chatHistoryRef.current) {
      chatHistoryRef.current.scrollTop = chatHistoryRef.current.scrollHeight;
    }
  }, [messages, isTyping]);

  useEffect(() => {
    if (logsEndRef.current) {
      logsEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [toolLogs, systemLogs, critiqueMsg, approvalMessage]);

  // Establish WebSocket connection when conversation ID is set
  const connectWebSocket = (convId: string) => {
    if (socketRef.current) {
      socketRef.current.close();
    }

    const wsUrl = `ws://localhost:8000/ws/chat/${convId}/`;
    console.log(`Connecting to WebSocket: ${wsUrl}`);
    const ws = new WebSocket(wsUrl);
    socketRef.current = ws;

    ws.onopen = () => {
      console.log("WebSocket connected.");
      addSystemLog("WebSocket stream connected.");
    };

    ws.onmessage = (event) => {
      const payload = JSON.parse(event.data);
      const eventType = payload.event_type;
      const data = payload.data;

      console.log("Received WS Event:", eventType, data);
      handleWebSocketEvent(eventType, data);
    };

    ws.onclose = () => {
      console.log("WebSocket disconnected.");
      addSystemLog("WebSocket stream disconnected.");
    };
  };

  const addSystemLog = (msg: string) => {
    setSystemLogs(prev => [...prev, msg]);
  };

  const handleWebSocketEvent = (eventType: string, data: any) => {
    switch (eventType) {
      case 'planner_start':
        setActiveNode('planner');
        setPlanSteps([]);
        setCritiqueMsg(null);
        setRetryCount(0);
        addSystemLog("Planner Node initialized thinking...");
        break;

      case 'planner_plan':
        setActiveNode('planner');
        const planList = (data.plan || []).map((step: string) => ({
          name: step.split('_').map((w: string) => w.charAt(0).toUpperCase() + w.slice(1)).join(' '),
          status: 'pending' as const
        }));
        setPlanSteps(planList);
        addSystemLog(`Generated plan containing ${planList.length} steps.`);
        break;

      case 'executor_step_start':
        setActiveNode('executor');
        const stepIdx = data.step_index;
        setPlanSteps(prev => prev.map((step, i) => {
          if (i < stepIdx) return { ...step, status: 'completed' };
          if (i === stepIdx) return { ...step, status: 'active' };
          return { ...step, status: 'pending' };
        }));
        addSystemLog(`Executing step ${stepIdx + 1}: ${data.goal}`);
        break;

      case 'tool_execution':
        setActiveNode('executor');
        const newToolLog: ToolLog = {
          id: Date.now().toString() + Math.random().toString(),
          name: data.tool_name,
          args: data.tool_args,
          result: data.tool_result,
          status: data.status
        };
        setToolLogs(prev => [...prev, newToolLog]);
        addSystemLog(`Executed tool ${data.tool_name} successfully.`);
        break;

      case 'executor_step_end':
        setActiveNode('executor');
        addSystemLog(`Finished executing step: ${data.goal}`);
        break;

      case 'critic_start':
        setActiveNode('critic');
        addSystemLog("Critique Node started quality evaluation...");
        break;

      case 'critic_pass':
        setActiveNode('critic');
        setCritiqueMsg(null);
        addSystemLog("Step passed Critique verification successfully.");
        break;

      case 'critic_fail':
        setActiveNode('critic');
        setCritiqueMsg(data.critique);
        setRetryCount(data.retry_count);
        addSystemLog(`Critique failed. Triggering retry loop (${data.retry_count}/3).`);
        break;

      case 'critic_max_retries':
        setActiveNode('critic');
        setCritiqueMsg(`Max retries reached. ${data.critique}`);
        addSystemLog("Warning: Max retries exceeded. Forcing progress.");
        break;

      case 'approval_requested':
        setActiveNode('approval_wait');
        setApprovalMessage(data.message);
        addSystemLog("Workflow suspended. Waiting for human-in-the-loop approval.");
        break;

      case 'submit_start':
        setActiveNode('submit');
        addSystemLog("Submit Node initialized form dispatch...");
        break;

      case 'submit_end':
        setActiveNode('submit');
        addSystemLog("Application form submitted successfully!");
        break;

      case 'run_completed':
        setActiveNode('memory_extraction');
        addSystemLog("Memory Extractor triggered user preference updates.");
        addSystemLog("Workflow complete!");
        setTimeout(() => setActiveNode(null), 3000);
        break;

      default:
        break;
    }
  };

  const handleFormSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (isTyping) return;

    // Build constructed message text based on agent selection
    let messageText = '';
    if (agentType === 'job_search') {
      messageText = `Search for jobs matching Title: "${jobTitle}", Location: "${jobLocation}"`;
      if (jobKeywords) messageText += `, Keywords: "${jobKeywords}"`;
      if (jobCompany) messageText += `, Target Company: "${jobCompany}"`;
    } else if (agentType === 'resume_tailor') {
      messageText = `Tailor my resume for this description.\nResume:\n${resumeText}\nJob Description:\n${jobDescription}`;
    } else if (agentType === 'browser_headless') {
      messageText = `Navigate to URL: "${browserUrl}" and execute instructions: "${browserCommand}"`;
    } else {
      messageText = input.trim();
    }

    if (!messageText) return;

    // Clean previous logs and visualizer displays
    setToolLogs([]);
    setSystemLogs([]);
    setPlanSteps([]);
    setCritiqueMsg(null);
    setRetryCount(0);
    setApprovalMessage(null);
    setActiveNode('memory_injection');

    const userMsg: Message = { id: Date.now().toString(), role: 'user', content: messageText };
    setMessages(prev => [...prev, userMsg]);
    setInput('');
    setIsTyping(true);

    try {
      const isNew = !conversationId;
      const res = await axios.post('http://localhost:8000/api/chat/', {
        message: messageText,
        conversation_id: conversationId,
        agent_type: agentType === 'resume_tailor' ? 'JobReasoningAgent' : 'ResearchAgent',
        selected_provider: userSelectedProvider
      });

      const newConvId = res.data.conversation_id;
      setConversationId(newConvId);
      if (res.data.selected_model) {
        setActiveModel(res.data.selected_model);
        setActiveProvider(res.data.selected_provider);
      }
      
      if (isNew) {
        fetchConversations();
      }
      
      // Establish WebSocket push connection
      connectWebSocket(newConvId);

      const aiMsg: Message = { 
        id: (Date.now() + 1).toString(), 
        role: 'ai', 
        content: res.data.response,
        prompt_tokens: res.data.prompt_tokens,
        completion_tokens: res.data.completion_tokens,
        total_tokens: res.data.total_tokens,
        provider: res.data.selected_provider,
        model: res.data.selected_model
      };
      setMessages(prev => [...prev, aiMsg]);
    } catch (err) {
      console.error(err);
      setMessages(prev => [...prev, { 
        id: (Date.now() + 1).toString(), 
        role: 'ai', 
        content: "Sorry, I encountered an error connecting to the backend. Ensure Daphne/Django is running on port 8000." 
      }]);
      setActiveNode(null);
    } finally {
      setIsTyping(false);
    }
  };

  const handleApproval = async (approved: boolean) => {
    if (!conversationId) return;
    setApprovalMessage(null);
    addSystemLog(`Submitting human approval decision: ${approved ? 'APPROVED' : 'REJECTED'}`);
    setActiveNode('executor');

    try {
      const res = await axios.post('http://localhost:8000/api/chat/approve/', {
        conversation_id: conversationId,
        approved: approved
      });
      addSystemLog(`Approval response: ${res.data.message}`);
    } catch (err) {
      console.error(err);
      addSystemLog("Failed to submit approval choice.");
      setActiveNode('approval_wait');
    }
  };

  // Node position specs for pipeline graph SVG rendering
  const nodes = [
    { id: 'memory_injection', label: 'Memory Injected', x: 50 },
    { id: 'planner', label: 'Planner node', x: 140 },
    { id: 'executor', label: 'Executor node', x: 230 },
    { id: 'critic', label: 'Reflection critic', x: 320 },
    { id: 'approval_wait', label: 'Approval Gate', x: 410 },
    { id: 'submit', label: 'Form submit', x: 500 },
    { id: 'memory_extraction', label: 'Memory Extracted', x: 590 }
  ];

  const getNodeClass = (nodeId: string) => {
    if (activeNode === nodeId) return 'pipeline-node active';
    
    // Simple completion tracking logic
    const activeIndex = nodes.findIndex(n => n.id === activeNode);
    const selfIndex = nodes.findIndex(n => n.id === nodeId);
    if (activeIndex !== -1 && selfIndex < activeIndex) return 'pipeline-node completed';
    
    return 'pipeline-node';
  };

  return (
    <div className="v3-app-wrapper">
      {/* Nomad V3 Top Navigation Bar */}
      <nav className="v3-top-nav">
        <div className="nav-brand">
          <Sparkles size={18} color="#6366f1" />
          <span className="brand-name">NOMAD V3</span>
          <span className="brand-sub">Career Operating System</span>
        </div>
        <div className="nav-tabs">
          <button 
            type="button"
            className={`nav-tab ${activeTab === 'chat' ? 'active' : ''}`} 
            onClick={() => setActiveTab('chat')}
          >
            <Activity size={15} />
            <span>Agent Workspace</span>
          </button>
          <button 
            type="button"
            className={`nav-tab ${activeTab === 'knowledge_base' ? 'active' : ''}`} 
            onClick={() => setActiveTab('knowledge_base')}
          >
            <Database size={15} />
            <span>Knowledge Base</span>
          </button>
          <button 
            type="button"
            className={`nav-tab ${activeTab === 'tailor' ? 'active' : ''}`} 
            onClick={() => setActiveTab('tailor')}
          >
            <FileText size={15} />
            <span>ATS Tailor & LaTeX Engine</span>
          </button>
          <button 
            type="button"
            className={`nav-tab ${activeTab === 'versions' ? 'active' : ''}`} 
            onClick={() => setActiveTab('versions')}
          >
            <BarChart3 size={15} />
            <span>Versions & Tracker</span>
          </button>
          <button 
            type="button"
            className={`nav-tab ${activeTab === 'prospecting' ? 'active' : ''}`} 
            onClick={() => setActiveTab('prospecting')}
          >
            <Globe size={15} />
            <span>Prospecting Engine</span>
          </button>
        </div>
      </nav>

      {/* Tab 1: Original Agent & Chat Workspace */}
      {activeTab === 'chat' && (
        <div className="workspace-grid">
      {/* Sidebar: Past Conversations */}
      <div className="sidebar">
        <div className="sidebar-header">
          <button className="new-chat-btn" onClick={handleNewChat}>
            New Chat
          </button>
        </div>
        <div className="conversation-list">
          {conversations.map(conv => (
            <div 
              key={conv.id} 
              className={`history-item ${conversationId === conv.id ? 'active' : ''}`}
              onClick={() => handleSelectConversation(conv.id)}
            >
              <span className="history-title">{conv.title || "Untitled Chat"}</span>
              <span className="history-date">{new Date(conv.created_at).toLocaleDateString()}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Left Pane: Interactive Chat and Configuration Panels */}
      <div className="chat-pane">
        <header className="pane-header">
          <h1>AIOS Agent Hub</h1>
          <div className="header-badges">
            <select
              value={userSelectedProvider}
              onChange={(e) => setUserSelectedProvider(e.target.value)}
              className="model-override-select"
              title="Select LLM Provider (Default: Auto Router)"
            >
              <option value="auto">🤖 Auto (Intelligent Router)</option>
              <option value="gemini-flash">⚡ Gemini Flash (gemini-2.5-flash)</option>
              <option value="groq">⚡ Groq (mixtral-8x7b-32768)</option>
              <option value="cerebras">⚡ Cerebras (llama3.1-8b)</option>
              <option value="openrouter">⚡ OpenRouter (llama-3-8b-instruct)</option>
              <option value="ollama">⚡ Ollama (qwen3:8b)</option>
            </select>

            <button type="button" className="models-status-btn" onClick={fetchProvidersStatus} title="View Available Models & Router Health">
              <Layers size={14} color="#6366f1" />
              <span>Models & Router</span>
            </button>
            {activeModel && (
              <div className="model-badge" title={`Active Router Provider: ${activeProvider}`}>
                <Cpu size={14} color="#6366f1" />
                <span>{activeModel} ({activeProvider})</span>
              </div>
            )}
            <div className="status-badge">
              <Activity size={14} color={activeNode ? '#a78bfa' : '#9ca3af'} />
              {activeNode ? 'Executing V2 Run' : 'Ready'}
            </div>
          </div>
        </header>

        {/* Dropdown Agent Type Selector */}
        <div className="agent-selector-container">
          <span className="agent-label">Select Target Agent</span>
          <select 
            value={agentType} 
            onChange={(e) => setAgentType(e.target.value as AgentType)}
            className="agent-select"
          >
            <option value="general">Default AI OS Assistant (General Chat)</option>
            <option value="job_search">Job Search Agent (Research loop)</option>
            <option value="resume_tailor">Resume Customization Agent (Reasoning loop)</option>
            <option value="browser_headless">Headless Browser Agent (Playwright)</option>
          </select>
        </div>

        {/* Collapsible Parameter Accordion */}
        {agentType !== 'general' && (
          <div className="parameters-accordion">
            <button 
              type="button" 
              className="parameters-toggle-btn" 
              onClick={() => setShowParams(!showParams)}
            >
              <span>{agentType.toUpperCase().replace('_', ' ')} PARAMETERS</span>
              {showParams ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
            </button>
            {showParams && (
              <div className="parameters-form-content">
                {agentType === 'job_search' && (
                  <div>
                    <div className="form-grid">
                      <div className="form-field">
                        <label>Job Title</label>
                        <input 
                          type="text" 
                          value={jobTitle} 
                          onChange={(e) => setJobTitle(e.target.value)} 
                          placeholder="e.g. Python Developer" 
                        />
                      </div>
                      <div className="form-field">
                        <label>Location</label>
                        <input 
                          type="text" 
                          value={jobLocation} 
                          onChange={(e) => setJobLocation(e.target.value)} 
                          placeholder="e.g. Remote, NY" 
                        />
                      </div>
                      <div className="form-field">
                        <label>Key Tech Stack</label>
                        <input 
                          type="text" 
                          value={jobKeywords} 
                          onChange={(e) => setJobKeywords(e.target.value)} 
                          placeholder="e.g. React, Django" 
                        />
                      </div>
                      <div className="form-field">
                        <label>Target Company</label>
                        <input 
                          type="text" 
                          value={jobCompany} 
                          onChange={(e) => setJobCompany(e.target.value)} 
                          placeholder="e.g. Stripe, OpenAI" 
                        />
                      </div>
                    </div>
                  </div>
                )}

                {agentType === 'resume_tailor' && (
                  <div>
                    <div className="form-field full-width" style={{ marginBottom: '10px' }}>
                      <label>Raw Resume Content</label>
                      <textarea 
                        value={resumeText} 
                        onChange={(e) => setResumeText(e.target.value)} 
                        placeholder="Paste your raw markdown/text resume details here..." 
                      />
                    </div>
                    <div className="form-field full-width">
                      <label>Target Job Description</label>
                      <textarea 
                        value={jobDescription} 
                        onChange={(e) => setJobDescription(e.target.value)} 
                        placeholder="Paste the target job description to align against..." 
                      />
                    </div>
                  </div>
                )}

                {agentType === 'browser_headless' && (
                  <div>
                    <div className="form-field full-width" style={{ marginBottom: '10px' }}>
                      <label>Initial Navigation URL</label>
                      <input 
                        type="text" 
                        value={browserUrl} 
                        onChange={(e) => setBrowserUrl(e.target.value)} 
                        placeholder="e.g. https://news.ycombinator.com" 
                      />
                    </div>
                    <div className="form-field full-width">
                      <label>Scrape/Execution Commands</label>
                      <input 
                        type="text" 
                        value={browserCommand} 
                        onChange={(e) => setBrowserCommand(e.target.value)} 
                        placeholder="e.g. Extract the titles of the first 3 posts" 
                      />
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* Scrollable Chat Messages */}
        <div className="chat-history" ref={chatHistoryRef}>
          {messages.map(msg => (
            <div key={msg.id} className={`message ${msg.role}`}>
              <p dangerouslySetInnerHTML={{ __html: formatContent(msg.content) }} />
              {(msg.role === 'ai' || msg.role === 'assistant') && (msg.total_tokens !== undefined || msg.model) && (
                <div className="message-token-badge">
                  <Zap size={11} color="#6366f1" />
                  <span>
                    {msg.model || activeModel || 'Model'} ({msg.provider || activeProvider || 'router'})
                    {msg.total_tokens ? ` · ${msg.total_tokens} tokens (${msg.prompt_tokens || 0} in / ${msg.completion_tokens || 0} out)` : ''}
                  </span>
                </div>
              )}
            </div>
          ))}
          
          {isTyping && (
            <div className="typing-indicator">
              <span></span><span></span><span></span>
            </div>
          )}
        </div>

        <div className="input-area">
          <form onSubmit={handleFormSubmit} className="chat-input-form">
            <input 
              type="text" 
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={
                agentType === 'general' 
                  ? "Ask AIOS anything..." 
                  : `Parameters locked. Press Send to trigger ${agentType.replace('_', ' ')}.`
              }
              autoComplete="off"
              disabled={isTyping || agentType !== 'general'}
            />
            <button type="submit" className="btn-send" disabled={isTyping}>
              <Send size={20} />
            </button>
          </form>
        </div>
      </div>

      {/* Right Pane: Live Pipeline Graph & Tool execution log Workspace */}
      <div className="workspace-pane">
        <div className="workspace-header">
          <h2>
            <Globe size={18} color="#8b5cf6" />
            Live Execution Workspace
          </h2>
        </div>

        {/* SVG Node Pipeline Graph */}
        <div className="visualizer-container">
          <svg className="pipeline-svg" viewBox="0 0 640 100">
            {/* Draw Connecting Edges */}
            {nodes.slice(0, -1).map((node, i) => {
              const nextNode = nodes[i + 1];
              let edgeClass = 'pipeline-edge';
              if (activeNode === nextNode.id) {
                edgeClass += ' active';
              } else if (
                nodes.findIndex(n => n.id === activeNode) > i + 1 ||
                (!activeNode && i < nodes.length)
              ) {
                edgeClass += ' completed';
              }
              return (
                <line 
                  key={i} 
                  x1={node.x} 
                  y1={45} 
                  x2={nextNode.x} 
                  y2={45} 
                  className={edgeClass} 
                />
              );
            })}

            {/* Draw SVG Nodes */}
            {nodes.map(node => (
              <g 
                key={node.id} 
                className={getNodeClass(node.id)}
              >
                <circle cx={node.x} cy={45} r={16} />
                <text x={node.x} y={80}>{node.label}</text>
              </g>
            ))}
          </svg>
        </div>

        {/* Execution Stream Logs */}
        <div className="workspace-stream">
          {/* Plan Checkpoints Checklist */}
          {planSteps.length > 0 && (
            <div className="checkpoint-list-box">
              <h3>Workflow Checklist</h3>
              <div className="plan-checkpoints">
                {planSteps.map((step, idx) => (
                  <div key={idx} className={`checkpoint-item ${step.status}`}>
                    <div className="checkpoint-bullet">{idx + 1}</div>
                    <span>{step.name}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Real-time system notifications */}
          {systemLogs.map((log, idx) => (
            <div key={idx} className="system-log-line">
              [System] {log}
            </div>
          ))}

          {/* Critique Fail Warning */}
          {critiqueMsg && (
            <div className="critique-alert">
              <div className="critique-title">
                <AlertTriangle size={16} />
                Quality Controller Critique (Retry #{retryCount})
              </div>
              <div className="critique-body">{critiqueMsg}</div>
            </div>
          )}

          {/* Human-in-the-Loop Gate Resumption */}
          {approvalMessage && (
            <div className="approval-panel">
              <div className="approval-title">Approval Hold Triggered</div>
              <p className="approval-desc">{approvalMessage}</p>
              <div className="approval-btn-group">
                <button className="btn-approve" onClick={() => handleApproval(true)}>Approve Execution</button>
                <button className="btn-reject" onClick={() => handleApproval(false)}>Reject Run</button>
              </div>
            </div>
          )}

          {/* Tool execution logs */}
          {toolLogs.map(log => (
            <div key={log.id} className="tool-run-card">
              <div className="tool-run-header">
                <span className="tool-run-title">call: {log.name}()</span>
                <span className={`tool-run-status ${log.status}`}>{log.status}</span>
              </div>
              <div className="tool-run-body">
                <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>Arguments:</p>
                <div className="tool-code">{JSON.stringify(log.args, null, 2)}</div>
                <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '8px' }}>Response:</p>
                <div className="tool-code result">{log.result}</div>
              </div>
            </div>
          ))}

          <div ref={logsEndRef} />
        </div>
      </div>
      </div>
      )}

      {/* Tab 2: Professional Knowledge Base Manager */}
      {activeTab === 'knowledge_base' && (
        <div className="v3-tab-container">
          <div className="v3-panel-header">
            <div>
              <h2>Professional Knowledge Base</h2>
              <p>The single source of truth for your career experience, skills, and projects.</p>
            </div>
            <div className="profile-badge-pill">
              <span>{kbData?.years_of_experience || 5}+ Years Exp</span>
            </div>
          </div>

          <div className="v3-two-column">
            {/* Left Column: Raw Resume Ingestion Box */}
            <div className="v3-card">
              <div className="v3-card-title" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <Sparkles size={16} color="#6366f1" />
                  <span>Ingest & Normalize Resume</span>
                </div>
                <button type="button" onClick={handleResetProfile} className="v3-btn-danger" style={{ padding: '4px 8px', fontSize: '11px', backgroundColor: '#ef4444', color: '#fff', border: 'none', borderRadius: '4px', cursor: 'pointer' }}>
                  Reset Profile
                </button>
              </div>
              <p className="v3-text-muted">Upload your existing resume (PDF/DOCX) or paste raw text. AI will parse and segment it into PostgreSQL.</p>
              
              <div 
                className="v3-drag-drop-zone"
                onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => {
                  e.preventDefault();
                  if (e.dataTransfer.files && e.dataTransfer.files[0]) {
                    setIngestFile(e.dataTransfer.files[0]);
                  }
                }}
                style={{
                  border: '2px dashed #6366f1',
                  borderRadius: '8px',
                  padding: '20px',
                  textAlign: 'center',
                  cursor: 'pointer',
                  backgroundColor: '#f8fafc',
                  marginBottom: '15px'
                }}
                onClick={() => document.getElementById('resume-file-input')?.click()}
              >
                <input 
                  type="file" 
                  id="resume-file-input" 
                  style={{ display: 'none' }} 
                  accept=".pdf,.docx,.txt,.md"
                  onChange={(e) => {
                    if (e.target.files && e.target.files[0]) {
                      setIngestFile(e.target.files[0]);
                    }
                  }}
                />
                {ingestFile ? (
                  <div>
                    <strong style={{ color: '#6366f1' }}>{ingestFile.name}</strong>
                    <p style={{ fontSize: '12px', color: '#64748b', marginTop: '4px' }}>Click or drag to swap files</p>
                  </div>
                ) : (
                  <div>
                    <p style={{ fontWeight: '500', color: '#334155' }}>Drag & drop your resume file here</p>
                    <p style={{ fontSize: '12px', color: '#64748b', marginTop: '4px' }}>Supports PDF, DOCX, TXT, MD</p>
                  </div>
                )}
              </div>

              <div style={{ position: 'relative', textAlign: 'center', margin: '15px 0' }}>
                <span style={{ backgroundColor: '#fff', padding: '0 10px', fontSize: '12px', color: '#94a3b8', zIndex: 2, position: 'relative' }}>OR PASTE TEXT</span>
                <div style={{ position: 'absolute', top: '50%', left: 0, right: 0, height: '1px', backgroundColor: '#f1f5f9', zIndex: 1 }} />
              </div>

              <textarea 
                className="v3-textarea"
                rows={5}
                value={ingestText}
                onChange={(e) => setIngestText(e.target.value)}
                placeholder="Or paste raw resume text here..."
              />
              
              <button 
                type="button"
                className="v3-btn-primary" 
                onClick={() => handleIngestResume()}
                disabled={isIngesting}
                style={{ marginTop: '15px', width: '100%' }}
              >
                {isIngesting ? 'Ingesting into Knowledge Base...' : 'Parse & Ingest into Knowledge Base'}
              </button>
            </div>

            {/* Right Column: Profile Summary */}
            <div className="v3-card">
              <div className="v3-card-title">
                <Database size={16} color="#6366f1" />
                <span>Career Profile Overview</span>
              </div>
              <div className="v3-form-field">
                <label>Headline</label>
                <input type="text" readOnly value={kbData?.headline || "Senior Software Engineer | AI Infrastructure & Systems"} className="v3-input" />
              </div>
              <div className="v3-form-field" style={{ marginTop: '12px' }}>
                <label>Professional Summary</label>
                <textarea readOnly rows={4} value={kbData?.summary || "Full-stack senior software engineer specializing in AI agent orchestration systems, high-concurrency Python backends, and deterministic LaTeX resume compilation."} className="v3-textarea" />
              </div>
            </div>
          </div>

          {/* Section: Structured Experiences */}
          <div className="v3-section-title">
            <h3>Work Experiences ({kbData?.experiences?.length || 0})</h3>
          </div>
          <div className="v3-grid-cards">
            {kbData?.experiences?.map((exp: any) => (
              <div key={exp.id} className="v3-item-card">
                <div className="v3-item-header">
                  <div>
                    <h4>{exp.role}</h4>
                    <span className="v3-company-sub">{exp.company} ({exp.start_date} - {exp.end_date})</span>
                  </div>
                </div>
                <ul className="v3-bullet-list">
                  {exp.bullet_points?.map((b: string, i: number) => (
                    <li key={i}>{b}</li>
                  ))}
                </ul>
                <div className="v3-tag-row">
                  {exp.tech_stack?.map((t: string, i: number) => (
                    <span key={i} className="v3-tech-tag">{t}</span>
                  ))}
                </div>
              </div>
            ))}
          </div>

          {/* Section: Key Projects */}
          <div className="v3-section-title" style={{ marginTop: '24px' }}>
            <h3>Major Technical Projects ({kbData?.projects?.length || 0})</h3>
          </div>
          <div className="v3-grid-cards">
            {kbData?.projects?.map((proj: any) => (
              <div key={proj.id} className="v3-item-card">
                <h4>{proj.title}</h4>
                <p className="v3-proj-desc">{proj.description}</p>
                <div className="v3-tag-row">
                  {proj.tech_stack?.map((t: string, i: number) => (
                    <span key={i} className="v3-tech-tag">{t}</span>
                  ))}
                </div>
              </div>
            ))}
          </div>

          {/* Section: Structured Skills */}
          <div className="v3-section-title" style={{ marginTop: '24px' }}>
            <h3>Technical Skills Inventory ({kbData?.skills?.length || 0})</h3>
          </div>
          <div className="v3-card" style={{ marginBottom: '24px' }}>
            {kbData?.skills && kbData.skills.length > 0 ? (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                {kbData.skills.map((skill: any) => (
                  <span key={skill.id} className="v3-skill-pill present" style={{ backgroundColor: '#f1f5f9', border: '1px solid #cbd5e1', padding: '6px 12px', borderRadius: '20px', fontSize: '13px' }}>
                    <strong>{skill.name}</strong> <span style={{ color: '#64748b', fontSize: '11px' }}>({skill.proficiency})</span>
                  </span>
                ))}
              </div>
            ) : (
              <p className="v3-text-muted">No skills populated yet. Ingest your resume or add skills manually below.</p>
            )}
          </div>

          {/* Section: Manual Profile Enrichment */}
          <div className="v3-section-title" style={{ marginTop: '32px' }}>
            <h3>Manually Enrich Your Career Profile</h3>
          </div>
          <div className="v3-three-column-enrich" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '20px', marginBottom: '40px' }}>
            {/* Enrich Experience */}
            <div className="v3-card">
              <div className="v3-card-title">Add Work Experience</div>
              <div className="v3-form-field">
                <label>Company</label>
                <input type="text" className="v3-input" value={enrichExperience.company} onChange={e => setEnrichExperience({...enrichExperience, company: e.target.value})} placeholder="e.g. Google" />
              </div>
              <div className="v3-form-field" style={{ marginTop: '8px' }}>
                <label>Role</label>
                <input type="text" className="v3-input" value={enrichExperience.role} onChange={e => setEnrichExperience({...enrichExperience, role: e.target.value})} placeholder="e.g. Senior Software Engineer" />
              </div>
              <div className="v3-form-field" style={{ marginTop: '8px' }}>
                <label>Start / End Dates</label>
                <div style={{ display: 'flex', gap: '10px' }}>
                  <input type="text" className="v3-input" value={enrichExperience.start_date} onChange={e => setEnrichExperience({...enrichExperience, start_date: e.target.value})} placeholder="Jan 2022" />
                  <input type="text" className="v3-input" value={enrichExperience.end_date} onChange={e => setEnrichExperience({...enrichExperience, end_date: e.target.value})} placeholder="Present" />
                </div>
              </div>
              <div className="v3-form-field" style={{ marginTop: '8px' }}>
                <label>Bullet Points (one per line)</label>
                <textarea className="v3-textarea" rows={3} value={enrichExperience.bullet_points} onChange={e => setEnrichExperience({...enrichExperience, bullet_points: e.target.value})} placeholder="Architected scalable telemetry system..." />
              </div>
              <div className="v3-form-field" style={{ marginTop: '8px' }}>
                <label>Tech Stack (comma separated)</label>
                <input type="text" className="v3-input" value={enrichExperience.tech_stack} onChange={e => setEnrichExperience({...enrichExperience, tech_stack: e.target.value})} placeholder="Python, Redis, GCP" />
              </div>
              <button type="button" className="v3-btn-primary" style={{ marginTop: '12px', width: '100%' }} onClick={handleEnrichExperience}>
                Add Experience
              </button>
            </div>

            {/* Enrich Project */}
            <div className="v3-card">
              <div className="v3-card-title">Add Evidentiary Project</div>
              <div className="v3-form-field">
                <label>Project Title</label>
                <input type="text" className="v3-input" value={enrichProject.title} onChange={e => setEnrichProject({...enrichProject, title: e.target.value})} placeholder="e.g. Billing Engine" />
              </div>
              <div className="v3-form-field" style={{ marginTop: '8px' }}>
                <label>Description</label>
                <textarea className="v3-textarea" rows={2} value={enrichProject.description} onChange={e => setEnrichProject({...enrichProject, description: e.target.value})} placeholder="High throughput distributed system..." />
              </div>
              <div className="v3-form-field" style={{ marginTop: '8px' }}>
                <label>Impact Metrics (one per line)</label>
                <textarea className="v3-textarea" rows={2} value={enrichProject.impact_metrics} onChange={e => setEnrichProject({...enrichProject, impact_metrics: e.target.value})} placeholder="Reduced latency by 45%" />
              </div>
              <div className="v3-form-field" style={{ marginTop: '8px' }}>
                <label>Tech Stack (comma separated)</label>
                <input type="text" className="v3-input" value={enrichProject.tech_stack} onChange={e => setEnrichProject({...enrichProject, tech_stack: e.target.value})} placeholder="Go, Kafka, PostgreSQL" />
              </div>
              <button type="button" className="v3-btn-primary" style={{ marginTop: '12px', width: '100%' }} onClick={handleEnrichProject}>
                Add Project
              </button>
            </div>

            {/* Enrich Skill */}
            <div className="v3-card">
              <div className="v3-card-title">Add Skill Tag</div>
              <div className="v3-form-field">
                <label>Skill Name</label>
                <input type="text" className="v3-input" value={enrichSkill.name} onChange={e => setEnrichSkill({...enrichSkill, name: e.target.value})} placeholder="e.g. Kubernetes" />
              </div>
              <div className="v3-form-field" style={{ marginTop: '8px' }}>
                <label>Category</label>
                <input type="text" className="v3-input" value={enrichSkill.category} onChange={e => setEnrichSkill({...enrichSkill, category: e.target.value})} placeholder="e.g. Cloud & DevOps" />
              </div>
              <div className="v3-form-field" style={{ marginTop: '8px' }}>
                <label>Proficiency</label>
                <select className="v3-input" value={enrichSkill.proficiency} onChange={e => setEnrichSkill({...enrichSkill, proficiency: e.target.value})}>
                  <option value="Expert">Expert</option>
                  <option value="Advanced">Advanced</option>
                  <option value="Intermediate">Intermediate</option>
                  <option value="Novice">Novice</option>
                </select>
              </div>
              <button type="button" className="v3-btn-primary" style={{ marginTop: '12px', width: '100%' }} onClick={handleEnrichSkill}>
                Add Skill
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Tab 3: ATS Tailor & Deterministic LaTeX Engine */}
      {activeTab === 'tailor' && (
        <div className="v3-tab-container">
          <div className="v3-panel-header">
            <div>
              <h2>ATS Tailor & Deterministic LaTeX Engine</h2>
              <p>LLM selects and prioritizes facts from Knowledge Base ➔ Deterministic LaTeX renders PDF. Zero fabrication.</p>
            </div>
          </div>

          <div className="v3-two-column">
            {/* Input Job Description */}
            <div className="v3-card">
              <div className="v3-card-title">
                <FileText size={16} color="#6366f1" />
                <span>Target Job Posting</span>
              </div>
              
              <div style={{ display: 'flex', gap: '10px', marginBottom: '15px', alignItems: 'flex-end' }}>
                <div style={{ flex: 1 }}>
                  <label style={{ display: 'block', fontSize: '12px', fontWeight: 600, color: 'var(--text-muted)', marginBottom: '5px' }}>Resume Style Template</label>
                  <select 
                    className="v3-input" 
                    value={selectedTemplateName} 
                    onChange={e => setSelectedTemplateName(e.target.value)}
                    style={{ height: '38px', width: '100%' }}
                  >
                    <option value="modern">Modern (Default)</option>
                    {templates.map(t => (
                      <option key={t.id} value={t.name}>{t.name}</option>
                    ))}
                  </select>
                </div>
                <button
                  type="button"
                  className="v3-btn-subtle"
                  onClick={() => setShowUploadTemplateCard(!showUploadTemplateCard)}
                  style={{ height: '38px', minWidth: 'auto', padding: '0 15px' }}
                >
                  {showUploadTemplateCard ? 'Cancel' : '➕ Upload Template'}
                </button>
              </div>

              {/* Upload Custom Template Section */}
              {showUploadTemplateCard && (
                <div style={{ background: 'rgba(255,255,255,0.03)', padding: '15px', borderRadius: '6px', marginBottom: '20px', border: '1px dashed var(--border-color)' }}>
                  <h4 style={{ margin: '0 0 10px 0', fontSize: '13px' }}>Upload Custom Template (.tex or .pdf)</h4>
                  <div style={{ marginBottom: '10px' }}>
                    <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '3px' }}>Template Name</label>
                    <input 
                      type="text" 
                      className="v3-input"
                      placeholder="e.g. Elegant, Compact, Minimal"
                      value={newTemplateName}
                      onChange={e => setNewTemplateName(e.target.value)}
                      style={{ height: '32px' }}
                    />
                  </div>
                  <div style={{ marginBottom: '10px' }}>
                    <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '3px' }}>Upload File</label>
                    <input 
                      type="file" 
                      accept=".tex,.pdf"
                      onChange={e => setNewTemplateFile(e.target.files ? e.target.files[0] : null)}
                      style={{ fontSize: '12px', display: 'block', margin: '5px 0' }}
                    />
                    <span style={{ display: 'block', fontSize: '10px', color: 'var(--text-muted)', marginTop: '2px' }}>
                      Tip: If uploading a PDF, Nomad will translate it to a LaTeX template.
                    </span>
                  </div>
                  <div style={{ marginBottom: '10px' }}>
                    <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '3px' }}>Or Paste Raw LaTeX Jinja2 Source</label>
                    <textarea 
                      className="v3-textarea"
                      rows={5}
                      placeholder="Paste raw LaTeX with Jinja2 loops..."
                      value={newTemplateSource}
                      onChange={e => setNewTemplateSource(e.target.value)}
                      disabled={!!newTemplateFile}
                    />
                  </div>
                  <button 
                    type="button" 
                    className="v3-btn-primary" 
                    onClick={handleUploadTemplate}
                    disabled={isUploadingTemplate}
                    style={{ width: '100%', height: '32px', fontSize: '12px' }}
                  >
                    {isUploadingTemplate ? 'Processing & Saving Template...' : 'Save Template'}
                  </button>
                </div>
              )}

              <textarea 
                className="v3-textarea"
                rows={12}
                value={tailorJobText}
                onChange={(e) => setTailorJobText(e.target.value)}
                placeholder="Paste Target Job Description (or Job URL)..."
              />
              <button 
                type="button"
                className="v3-btn-primary" 
                onClick={handleTailorResume}
                disabled={isTailoring}
                style={{ marginTop: '12px' }}
              >
                {isTailoring ? 'Compiling Tailored LaTeX Spec...' : '⚡ Run ATS Analysis & Generate Tailored Resume'}
              </button>
            </div>

            {/* ATS Analysis Output */}
            <div className="v3-card">
              <div className="v3-card-title">
                <Sliders size={16} color="#6366f1" />
                <span>ATS Gap Analysis & Match Score</span>
              </div>
              {tailoredResult ? (
                <div>
                  <div className="ats-score-display">
                    <span className="ats-score-num">{tailoredResult.ats_score}%</span>
                    <span className="ats-score-label">ATS Keyword Match Score</span>
                  </div>
                  
                  <div className="ats-skills-block">
                    <label>Matched Skills ({tailoredResult.ats_report?.present_skills?.length || 0}):</label>
                    <div className="v3-tag-row">
                      {tailoredResult.ats_report?.present_skills?.map((s: string, i: number) => (
                        <span key={i} className="v3-skill-pill present"><Check size={11} /> {s}</span>
                      ))}
                    </div>
                  </div>

                  <div className="ats-skills-block" style={{ marginTop: '10px' }}>
                    <label>Missing / Skill Gaps:</label>
                    <div className="v3-tag-row">
                      {tailoredResult.ats_report?.missing_skills?.map((s: string, i: number) => (
                        <span key={i} className="v3-skill-pill missing"><X size={11} /> {s}</span>
                      ))}
                    </div>
                  </div>
                </div>
              ) : (
                <div className="v3-empty-state">
                  <p>Paste a job description on the left and click Generate to run ATS gap analysis and compile LaTeX.</p>
                </div>
              )}
            </div>
          </div>

          {/* Generated LaTeX Code & PDF Viewer */}
          {tailoredResult && (
            <div className="v3-card" style={{ marginTop: '20px' }}>
              <div className="v3-card-header-bar">
                <div className="v3-card-title">
                  <Code size={16} color="#6366f1" />
                  <span>Deterministic LaTeX Code ({tailoredResult.version_name})</span>
                </div>
                <div className="view-toggle-btns">
                  <button 
                    type="button" 
                    className={`toggle-btn ${activeLatexView === 'preview' ? 'active' : ''}`}
                    onClick={() => setActiveLatexView('preview')}
                  >
                    Formatted Text
                  </button>
                  <button 
                    type="button" 
                    className={`toggle-btn ${activeLatexView === 'latex' ? 'active' : ''}`}
                    onClick={() => setActiveLatexView('latex')}
                  >
                    LaTeX Source (.tex)
                  </button>
                </div>
              </div>

              {activeLatexView === 'latex' ? (
                <pre className="v3-code-block">{tailoredResult.latex_code}</pre>
              ) : (
                <div className="v3-formatted-preview">
                  <h3>{tailoredResult.structured_spec?.header?.full_name}</h3>
                  <p className="sub">{tailoredResult.structured_spec?.header?.headline}</p>
                  <h4>Summary</h4>
                  <p>{tailoredResult.structured_spec?.summary}</p>
                  <h4>Experience Highlights</h4>
                  {tailoredResult.structured_spec?.experiences?.map((e: any, i: number) => (
                    <div key={i} className="prev-exp">
                      <strong>{e.role} at {e.company}</strong> ({e.start_date} - {e.end_date})
                      <ul>
                        {e.bullet_points?.map((b: string, j: number) => (
                          <li key={j}>{b}</li>
                        ))}
                      </ul>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Tab 4: Resume Versions Store & Application Tracker */}
      {activeTab === 'versions' && (
        <div className="v3-tab-container">
          <div className="v3-panel-header">
            <div>
              <h2>Immutable Resume Versions & Application Tracker</h2>
              <p>Every generated resume is saved as an immutable version linked to its job application history.</p>
            </div>
          </div>

          <div className="v3-card">
            <div className="v3-card-title">
              <BarChart3 size={16} color="#6366f1" />
              <span>Generated Resume Versions ({resumeVersions.length})</span>
            </div>
            <table className="v3-table">
              <thead>
                <tr>
                  <th>Version Name</th>
                  <th>Target Company</th>
                  <th>ATS Match</th>
                  <th>LLM Provider</th>
                  <th>Latency</th>
                  <th>Created At</th>
                </tr>
              </thead>
              <tbody>
                {resumeVersions.map((v: any) => (
                  <tr key={v.id}>
                    <td className="font-weight-600">{v.version_name}</td>
                    <td>{v.company_name}</td>
                    <td>
                      <span className="v3-ats-pill">{v.ats_score}%</span>
                    </td>
                    <td>{v.llm_provider || 'router'}</td>
                    <td>{v.generation_latency_ms}ms</td>
                    <td>{new Date(v.created_at).toLocaleDateString()}</td>
                  </tr>
                ))}
                {resumeVersions.length === 0 && (
                  <tr>
                    <td colSpan={6} style={{ textAlign: 'center', color: 'var(--text-muted)' }}>
                      No resume versions generated yet. Use the ATS Tailor tab to compile your first tailored resume.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          <div className="v3-card" style={{ marginTop: '20px' }}>
            <div className="v3-card-title">
              <Sliders size={16} color="#6366f1" />
              <span>Application Pipeline Tracker ({applications.length})</span>
            </div>
            <table className="v3-table">
              <thead>
                <tr>
                  <th>Target Company</th>
                  <th>Job Title</th>
                  <th>Attached Resume</th>
                  <th>ATS Match</th>
                  <th>Pipeline Status</th>
                </tr>
              </thead>
              <tbody>
                {applications.map((app: any) => (
                  <tr key={app.id}>
                    <td className="font-weight-600">{app.company_name}</td>
                    <td>{app.job_title}</td>
                    <td>{app.resume_version}</td>
                    <td><span className="v3-ats-pill">{app.ats_score}%</span></td>
                    <td><span className="v3-status-tag">{app.status}</span></td>
                  </tr>
                ))}
                {applications.length === 0 && (
                  <tr>
                    <td colSpan={5} style={{ textAlign: 'center', color: 'var(--text-muted)' }}>
                      No active job applications tracked yet.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Tab 5: Lead Generation Prospecting Engine */}
      {activeTab === 'prospecting' && (
        <div className="v3-tab-container animate-fade-in">
          <div className="v3-panel-header">
            <div>
              <h2>Operational Lead Prospecting & Suitability Engine</h2>
              <p>Discover businesses in targeted areas and qualify them automatically for Route Optimization suitability.</p>
            </div>
            <button 
              type="button" 
              className="v3-btn-danger" 
              onClick={handleResetProspecting}
            >
              Clear CRM Leads
            </button>
          </div>

          {/* Controls Panel Card */}
          <div className="v3-card" style={{ marginBottom: '20px' }}>
            <div className="v3-card-title">
              <Globe size={16} color="#6366f1" />
              <span>Launch Lead Discovery Run</span>
            </div>
            <div style={{ display: 'flex', gap: '15px', marginTop: '10px' }}>
              <div style={{ flex: 1 }}>
                <label style={{ display: 'block', fontSize: '12px', fontWeight: 600, color: 'var(--text-muted)', marginBottom: '5px' }}>Business Sector / Keyword</label>
                <input 
                  type="text" 
                  className="v3-input" 
                  placeholder="e.g. Courier Service, Plumbing, Waste Collection" 
                  value={prospectKeyword}
                  onChange={e => setProspectKeyword(e.target.value)}
                />
              </div>
              <div style={{ flex: 1 }}>
                <label style={{ display: 'block', fontSize: '12px', fontWeight: 600, color: 'var(--text-muted)', marginBottom: '5px' }}>Target Location</label>
                <input 
                  type="text" 
                  className="v3-input" 
                  placeholder="e.g. Manchester, London, Leeds" 
                  value={prospectLocation}
                  onChange={e => setProspectLocation(e.target.value)}
                />
              </div>
              <div style={{ display: 'flex', alignItems: 'flex-end' }}>
                <button 
                  type="button" 
                  className="v3-btn" 
                  onClick={handleRunProspecting}
                  disabled={isProspecting}
                  style={{ height: '40px', padding: '0 25px' }}
                >
                  {isProspecting ? 'Searching & Scrutinizing...' : 'Discover & Qualify'}
                </button>
              </div>
            </div>
          </div>

          {/* Filters Bar Card */}
          <div className="v3-card" style={{ marginBottom: '20px', padding: '15px' }}>
            <div className="v3-card-title" style={{ marginBottom: '10px' }}>
              <Sliders size={16} color="#6366f1" />
              <span>Filter Leads Directory</span>
            </div>
            <div style={{ display: 'flex', gap: '15px' }}>
              <div style={{ flex: 1 }}>
                <label style={{ display: 'block', fontSize: '11px', fontWeight: 600, color: 'var(--text-muted)', marginBottom: '3px' }}>Category</label>
                <select 
                  className="v3-input" 
                  value={prospectFilterCategory} 
                  onChange={e => setProspectFilterCategory(e.target.value)}
                  style={{ height: '35px', padding: '0 10px' }}
                >
                  <option value="">All Categories</option>
                  {prospectCategoriesList.map(cat => (
                    <option key={cat} value={cat}>{cat}</option>
                  ))}
                </select>
              </div>
              <div style={{ flex: 1 }}>
                <label style={{ display: 'block', fontSize: '11px', fontWeight: 600, color: 'var(--text-muted)', marginBottom: '3px' }}>Location</label>
                <input 
                  type="text" 
                  className="v3-input" 
                  placeholder="Filter by city/address..."
                  value={prospectFilterLocation}
                  onChange={e => setProspectFilterLocation(e.target.value)}
                  style={{ height: '35px', padding: '0 10px' }}
                />
              </div>
              <div style={{ flex: 1 }}>
                <label style={{ display: 'block', fontSize: '11px', fontWeight: 600, color: 'var(--text-muted)', marginBottom: '3px' }}>Min Suitability Score</label>
                <select 
                  className="v3-input" 
                  value={prospectFilterScore} 
                  onChange={e => setProspectFilterScore(e.target.value)}
                  style={{ height: '35px', padding: '0 10px' }}
                >
                  <option value="">All Scores</option>
                  <option value="8">8.0+ (Excellent)</option>
                  <option value="5">5.0+ (Moderate)</option>
                  <option value="3">3.0+ (Low)</option>
                </select>
              </div>
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: selectedLead ? '1.4fr 1.6fr' : '1fr', gap: '20px', alignItems: 'start' }}>
            {/* Leads Table Card (Left Column, Scrollable) */}
            <div className="v3-card">
              <div className="v3-card-title">
                <Database size={16} color="#6366f1" />
                <span>CRM Qualified Leads Directory ({prospectTotalCount} total leads)</span>
              </div>
              
              <div style={{ maxHeight: '60vh', overflowY: 'auto', border: '1px solid var(--border-color)', borderRadius: '6px' }}>
                <table className="v3-table">
                  <thead>
                    <tr>
                      <th>Business Name</th>
                      <th>Category</th>
                      <th>Location</th>
                      <th>Lead Score</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {prospectLeads.map((lead: any) => {
                      const score = lead.analysis?.lead_score || 0;
                      const scoreColor = score >= 8 ? '#10b981' : score >= 5 ? '#f59e0b' : '#ef4444';
                      return (
                        <tr 
                          key={lead.id} 
                          className={selectedLead?.id === lead.id ? 'active-row' : ''} 
                          style={{ cursor: 'pointer' }} 
                          onClick={() => setSelectedLead(lead)}
                        >
                          <td className="font-weight-600">
                            {lead.name}
                            {lead.website && (
                              <a href={lead.website} target="_blank" rel="noreferrer" style={{ marginLeft: '8px', color: '#6366f1', fontSize: '11px' }}>
                                Visit Website
                              </a>
                            )}
                          </td>
                          <td>{lead.category || 'N/A'}</td>
                          <td>{lead.address || 'N/A'}</td>
                          <td>
                            <span className="v3-ats-pill" style={{ backgroundColor: `${scoreColor}15`, color: scoreColor, borderColor: `${scoreColor}40` }}>
                              {score.toFixed(1)}/10
                            </span>
                          </td>
                          <td>
                            <button 
                              type="button" 
                              className="v3-btn-subtle" 
                              onClick={(e) => {
                                e.stopPropagation();
                                setSelectedLead(lead);
                              }}
                            >
                              Inspect
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                    {prospectLeads.length === 0 && (
                      <tr>
                        <td colSpan={5} style={{ textAlign: 'center', color: 'var(--text-muted)', padding: '30px' }}>
                          No matching leads found.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>

              {/* Pagination Controls */}
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '15px', paddingTop: '15px', borderTop: '1px solid var(--border-color)' }}>
                <span style={{ fontSize: '13px', color: 'var(--text-muted)' }}>
                  Page {prospectPage} of {prospectTotalPages} ({prospectTotalCount} total leads)
                </span>
                <div style={{ display: 'flex', gap: '10px' }}>
                  <button 
                    type="button" 
                    className="v3-btn-subtle" 
                    disabled={prospectPage <= 1} 
                    onClick={() => fetchProspectLeads(prospectPage - 1)}
                    style={{ padding: '5px 12px', minWidth: 'auto' }}
                  >
                    Previous
                  </button>
                  <button 
                    type="button" 
                    className="v3-btn-subtle" 
                    disabled={prospectPage >= prospectTotalPages} 
                    onClick={() => fetchProspectLeads(prospectPage + 1)}
                    style={{ padding: '5px 12px', minWidth: 'auto' }}
                  >
                    Next
                  </button>
                </div>
              </div>
            </div>

            {/* Leads Side Inspector Panel (Right Column, Sticky) */}
            {selectedLead && (
              <div style={{ position: 'sticky', top: '10px' }}>
                <div className="v3-card animate-fade-in" style={{ height: 'fit-content', maxHeight: '82vh', overflowY: 'auto' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid var(--border-color)', paddingBottom: '10px', marginBottom: '15px' }}>
                    <h3 style={{ margin: 0 }}>Lead Suitability Profile</h3>
                    <button type="button" className="v3-btn-subtle" style={{ minWidth: 'auto', padding: '4px 8px' }} onClick={() => setSelectedLead(null)}>X</button>
                  </div>
                  
                  <h2 style={{ fontSize: '20px', margin: '0 0 5px 0' }}>{selectedLead.name}</h2>
                  <p style={{ color: 'var(--text-muted)', fontSize: '13px', margin: '0 0 15px 0' }}>{selectedLead.address}</p>

                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px', marginBottom: '20px' }}>
                    <div style={{ background: 'rgba(255,255,255,0.03)', padding: '10px', borderRadius: '6px' }}>
                      <span style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)' }}>Lead Score</span>
                      <span style={{ fontSize: '24px', fontWeight: 700, color: (selectedLead.analysis?.lead_score || 0) >= 8 ? '#10b981' : '#f59e0b' }}>
                        {selectedLead.analysis?.lead_score?.toFixed(1) || 'N/A'}/10
                      </span>
                    </div>
                    <div style={{ background: 'rgba(255,255,255,0.03)', padding: '10px', borderRadius: '6px' }}>
                      <span style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)' }}>Fleet Size Estimate</span>
                      <span style={{ fontSize: '18px', fontWeight: 600 }}>{selectedLead.analysis?.fleet_size_estimate || 'Unknown'}</span>
                    </div>
                  </div>

                  <div style={{ marginBottom: '20px' }}>
                    <h4 style={{ margin: '0 0 8px 0', fontSize: '13px', fontWeight: 600 }}>Operational Suitability Checks</h4>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px' }}>
                        <span>Has Deliveries:</span>
                        <span style={{ fontWeight: 600, color: selectedLead.analysis?.has_delivery ? '#10b981' : '#ef4444' }}>
                          {selectedLead.analysis?.has_delivery ? 'YES' : 'NO'}
                        </span>
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px' }}>
                        <span>Has Appointment Scheduling:</span>
                        <span style={{ fontWeight: 600, color: selectedLead.analysis?.has_scheduling ? '#10b981' : '#ef4444' }}>
                          {selectedLead.analysis?.has_scheduling ? 'YES' : 'NO'}
                        </span>
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px' }}>
                        <span>Needs Daily Route Planning:</span>
                        <span style={{ fontWeight: 600, color: selectedLead.analysis?.needs_routing ? '#10b981' : '#ef4444' }}>
                          {selectedLead.analysis?.needs_routing ? 'YES' : 'NO'}
                        </span>
                      </div>
                    </div>
                  </div>

                  <div style={{ marginBottom: '20px' }}>
                    <h4 style={{ margin: '0 0 8px 0', fontSize: '13px', fontWeight: 600 }}>Qualitative Summary</h4>
                    <p style={{ fontSize: '13px', lineHeight: 1.5, background: 'rgba(255,255,255,0.02)', padding: '10px', borderRadius: '6px', borderLeft: '3px solid #6366f1' }}>
                      {selectedLead.analysis?.description || 'No summary extracted.'}
                    </p>
                    <p style={{ fontSize: '12px', fontStyle: 'italic', marginTop: '10px', color: 'var(--text-muted)' }}>
                      Reasoning: {selectedLead.analysis?.lead_score_reason || 'N/A'}
                    </p>
                  </div>

                  <div>
                    <h4 style={{ margin: '0 0 8px 0', fontSize: '13px', fontWeight: 600 }}>Contact Information ({selectedLead.contacts?.length || 0})</h4>
                    {selectedLead.contacts?.map((con: any, i: number) => (
                      <div key={i} style={{ padding: '8px', border: '1px solid var(--border-color)', borderRadius: '4px', marginBottom: '8px', fontSize: '13px' }}>
                        {con.email !== 'linkedin@placeholder.com' ? (
                          <div>
                            <strong>Email:</strong> {con.email}
                          </div>
                        ) : (
                          <div>
                            <strong>LinkedIn URL:</strong> <a href={con.linkedin} target="_blank" rel="noreferrer" style={{ color: '#6366f1' }}>View Company Profile</a>
                          </div>
                        )}
                      </div>
                    ))}
                    {selectedLead.contacts?.length === 0 && (
                      <p style={{ fontSize: '13px', color: 'var(--text-muted)', fontStyle: 'italic' }}>
                        No direct contact emails or social links extracted from homepage footer.
                      </p>
                    )}
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Available Models & Router Status Overlay Modal */}
      {showModelsModal && providersData && (
        <div className="modal-backdrop" onClick={() => setShowModelsModal(false)}>
          <div className="modal-content" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h2>Available LLM Providers & Router Status</h2>
              <button type="button" className="modal-close-btn" onClick={() => setShowModelsModal(false)}>
                <X size={18} />
              </button>
            </div>
            <div className="modal-body">
              <h3>Configured Provider Health</h3>
              <div className="providers-grid">
                {providersData.providers.map(p => (
                  <div key={p.key} className={`provider-card ${p.status}`}>
                    <div className="provider-card-header">
                      <span className="provider-name">{p.key.toUpperCase()}</span>
                      <span className={`status-pill ${p.status}`}>
                        {p.status === 'healthy' && <CheckCircle2 size={12} />}
                        {p.status !== 'healthy' && <XCircle size={12} />}
                        {p.status === 'healthy' ? 'Healthy' : p.status === 'missing_key' ? 'No Key' : 'Cooldown'}
                      </span>
                    </div>
                    <div className="provider-card-body">
                      <div className="info-row">
                        <span className="info-label">Configured Model:</span>
                        <span className="info-val">{p.model_name}</span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>

              <h3>Fallback Priority Waterlines</h3>
              <div className="waterlines-container">
                {Object.entries(providersData.fallbacks).map(([tier, list]) => (
                  <div key={tier} className="waterline-row">
                    <span className="tier-tag">{tier.toUpperCase()} TIER:</span>
                    <span className="waterline-list">{list.join(' ➔ ')}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

const formatContent = (content: string) => {
  if (!content) return '';
  return content
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/\n/g, '<br/>');
};

export default App;
