<script setup lang="ts">
import { ref, onBeforeUnmount, onMounted, nextTick, computed } from 'vue'
import axios from 'axios'
import { marked } from 'marked'

// Types
type TaskStatus = 'queued' | 'running' | 'cancelling' | 'completed' | 'failed' | 'cancelled'

interface Message {
  role: 'user' | 'ai' | 'system'
  content: string
  logs?: LogItem[]
  files?: FileItem[]
  timestamp?: number
  taskId?: string
  taskStatus?: TaskStatus
  taskError?: string
  retryOf?: string
}

interface LogItem {
  type: string
  title: string
  details: any
  timestamp: string
}

interface FileItem {
  name: string
  path: string
  url: string
}

interface TaskRecord {
  task_id: string
  thread_id: string
  session_id?: string
  session_dir?: string
  status: TaskStatus
  error?: string | null
}

interface SessionMessage {
  role: 'user' | 'assistant' | 'system'
  content: string
  timestamp?: string
}

interface SessionRecord {
  session_id: string
  thread_id: string
  title: string
  created_at: string
  updated_at: string
  last_message_preview?: string
  message_count?: number
  session_dir?: string
  messages?: SessionMessage[]
}

// State
const API_BASE = 'http://127.0.0.1:8001'
const WS_BASE = 'ws://127.0.0.1:8001'
const TASK_POLL_INTERVAL_MS = 1500
const SESSION_STORAGE_KEY = 'deep_search_active_session_id'

const inputQuery = ref('')
const messages = ref<Message[]>([])
const status = ref<'idle' | 'running'>('idle')
const socket = ref<WebSocket | null>(null)
const currentSessionPath = ref('')
const currentSessionUrl = ref('')
const messagesEndRef = ref<HTMLElement | null>(null)
const isWelcomeScreen = computed(() => messages.value.length === 0)
const isSidebarOpen = ref(false)
const fileList = ref<any[]>([])
const fileInputRef = ref<HTMLInputElement | null>(null)
const selectedFiles = ref<File[]>([])
const sessions = ref<SessionRecord[]>([])
const activeSessionId = ref<string>('')
const currentThreadId = ref('')
const activeTaskId = ref<string | null>(null)
let taskPollTimer: ReturnType<typeof setTimeout> | null = null
let shouldReconnectWs = true

// Helper: Scroll to bottom
const scrollToBottom = async () => {
  await nextTick()
  if (messagesEndRef.value) {
    messagesEndRef.value.scrollIntoView({ behavior: 'smooth' })
  }
}

const toUiRole = (role: string): Message['role'] => {
  if (role === 'assistant') return 'ai'
  if (role === 'system') return 'system'
  return 'user'
}

const hydrateMessagesFromSession = (session: SessionRecord) => {
  const loaded = (session.messages || []).map((m) => ({
    role: toUiRole(m.role),
    content: m.content,
    timestamp: m.timestamp ? Date.parse(m.timestamp) : Date.now(),
  })) as Message[]
  messages.value = loaded
}

const applySessionWorkspace = (session: SessionRecord) => {
  if (session.session_dir) {
    currentSessionPath.value = session.session_dir
  } else if (session.thread_id) {
    currentSessionPath.value = ''
  }
  if (currentSessionPath.value) {
    const parts = currentSessionPath.value.split(/output[\\/]/)
    const rel = parts[1]
    if (rel) {
      currentSessionUrl.value = `${API_BASE}/outputs/${rel.replace(/\\/g, '/')}`
    }
  }
}

const sortSessions = () => {
  sessions.value.sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''))
}

const refreshSessions = async () => {
  const res = await axios.get<{ sessions: SessionRecord[] }>(`${API_BASE}/api/sessions`)
  sessions.value = res.data.sessions || []
  sortSessions()
}

const reconnectWebSocketForThread = (threadId: string) => {
  shouldReconnectWs = false
  if (socket.value) {
    socket.value.close()
  }
  shouldReconnectWs = true
  currentThreadId.value = threadId
  connectWebSocket()
}

const loadSessionById = async (sessionId: string) => {
  const res = await axios.get<SessionRecord>(`${API_BASE}/api/sessions/${sessionId}`)
  const session = res.data
  activeSessionId.value = session.session_id
  localStorage.setItem(SESSION_STORAGE_KEY, session.session_id)
  hydrateMessagesFromSession(session)
  applySessionWorkspace(session)
  isSidebarOpen.value = true
  if (session.thread_id && session.thread_id !== currentThreadId.value) {
    reconnectWebSocketForThread(session.thread_id)
  } else if (!socket.value && session.thread_id) {
    reconnectWebSocketForThread(session.thread_id)
  }
  await fetchFiles()
  await scrollToBottom()
}

const createNewSession = async () => {
  const res = await axios.post<SessionRecord>(`${API_BASE}/api/sessions`, {})
  const session = res.data
  sessions.value.unshift(session)
  sortSessions()
  await loadSessionById(session.session_id)
}

const switchSession = async (sessionId: string) => {
  if (status.value === 'running') return
  clearTaskPollTimer()
  activeTaskId.value = null
  status.value = 'idle'
  await loadSessionById(sessionId)
}

const deleteSession = async (sessionId: string) => {
  if (status.value === 'running') return
  const ok = window.confirm('Delete this session? This will remove messages and related files.')
  if (!ok) return

  const wasActive = activeSessionId.value === sessionId
  await axios.delete(`${API_BASE}/api/sessions/${sessionId}`)
  await refreshSessions()

  if (!wasActive) return

  const nextSessionId = sessions.value[0]?.session_id
  if (nextSessionId) {
    await loadSessionById(nextSessionId)
  } else {
    messages.value = []
    currentSessionPath.value = ''
    currentSessionUrl.value = ''
    await createNewSession()
  }
}

const bootstrapSessions = async () => {
  await refreshSessions()
  if (sessions.value.length === 0) {
    await createNewSession()
    return
  }
  const savedId = localStorage.getItem(SESSION_STORAGE_KEY)
  const latestId = sessions.value[0]?.session_id || ''
  const preferred = savedId && sessions.value.some((s) => s.session_id === savedId)
    ? savedId
    : latestId
  if (preferred) {
    await loadSessionById(preferred)
  }
}

// Fetch Files
const fetchFiles = async () => {
  if (!currentSessionPath.value) return
  try {
    const res = await axios.get(`${API_BASE}/api/files`, {
      params: { path: currentSessionPath.value }
    })
    if (res.data.files) {
      fileList.value = res.data.files.map((f: any) => ({
        ...f,
        // 使用下载 API，传入绝对路径
        url: `${API_BASE}/api/download?path=${encodeURIComponent(f.path)}`
      }))
    }
  } catch (e) {
    console.error('Failed to fetch files', e)
  }
}

const isTaskTerminal = (taskStatus?: TaskStatus) => {
  return taskStatus === 'completed' || taskStatus === 'failed' || taskStatus === 'cancelled'
}

const isTaskCancelable = (taskStatus?: TaskStatus) => {
  return taskStatus === 'queued' || taskStatus === 'running' || taskStatus === 'cancelling'
}

const shortTaskId = (taskId?: string) => {
  if (!taskId) return ''
  return taskId.slice(0, 8)
}

const findLatestAiMessage = () => {
  return messages.value.slice().reverse().find((m) => m.role === 'ai')
}

const findAiMessageByTaskId = (taskId: string) => {
  return messages.value.find((m) => m.role === 'ai' && m.taskId === taskId)
}

const findTrackedAiMessage = () => {
  if (activeTaskId.value) {
    const tracked = findAiMessageByTaskId(activeTaskId.value)
    if (tracked) return tracked
  }
  return findLatestAiMessage()
}

const appendLog = (msg: Message | undefined, log: LogItem) => {
  if (!msg) return
  if (!msg.logs) msg.logs = []
  msg.logs.push(log)
}

const clearTaskPollTimer = () => {
  if (taskPollTimer) {
    clearTimeout(taskPollTimer)
    taskPollTimer = null
  }
}

const scheduleTaskPoll = (taskId: string, delayMs = TASK_POLL_INTERVAL_MS) => {
  clearTaskPollTimer()
  taskPollTimer = setTimeout(() => {
    void pollTaskStatus(taskId)
  }, delayMs)
}

const applyTaskRecord = (msg: Message | undefined, record: TaskRecord) => {
  if (!msg) return
  msg.taskStatus = record.status
  msg.taskError = record.error || undefined
  if (!msg.taskId) {
    msg.taskId = record.task_id
  }
  if ((record.status === 'failed' || record.status === 'cancelled') && !msg.content && record.error) {
    msg.content = record.error
  }
}

const pollTaskStatus = async (taskId: string) => {
  if (activeTaskId.value !== taskId) return
  try {
    const res = await axios.get<TaskRecord>(`${API_BASE}/api/task/${taskId}`)
    const record = res.data
    const msg = findAiMessageByTaskId(taskId)
    applyTaskRecord(msg, record)

    if (isTaskTerminal(record.status)) {
      if (activeTaskId.value === taskId) {
        activeTaskId.value = null
      }
      clearTaskPollTimer()
      status.value = 'idle'
      return
    }

    scheduleTaskPoll(taskId)
  } catch (err) {
    console.error('Task polling failed', err)
    if (activeTaskId.value === taskId) {
      scheduleTaskPoll(taskId, 3000)
    }
  }
}

const startTaskTracking = (taskId: string, msg: Message) => {
  activeTaskId.value = taskId
  msg.taskId = taskId
  msg.taskStatus = 'queued'
  msg.taskError = undefined
  scheduleTaskPoll(taskId, 600)
}

const canCancelTask = (msg: Message) => {
  return !!msg.taskId && isTaskCancelable(msg.taskStatus)
}

const canRetryTask = (msg: Message) => {
  return !!msg.taskId && isTaskTerminal(msg.taskStatus)
}

const cancelTask = async (msg: Message) => {
  if (!msg.taskId || !canCancelTask(msg)) return
  try {
    const res = await axios.post<TaskRecord>(`${API_BASE}/api/task/${msg.taskId}/cancel`)
    applyTaskRecord(msg, res.data)
    appendLog(msg, {
      type: 'info',
      title: `任务取消请求已发送：${shortTaskId(msg.taskId)}`,
      details: null,
      timestamp: new Date().toLocaleTimeString()
    })
    if (isTaskTerminal(res.data.status)) {
      if (activeTaskId.value === msg.taskId) {
        activeTaskId.value = null
        clearTaskPollTimer()
      }
      status.value = 'idle'
    }
  } catch (error: any) {
    console.error('Cancel task failed:', error)
    appendLog(msg, {
      type: 'error',
      title: '取消任务失败',
      details: error?.response?.data || error?.message || 'Unknown error',
      timestamp: new Date().toLocaleTimeString()
    })
  }
}

const retryTask = async (msg: Message) => {
  if (!msg.taskId || !canRetryTask(msg)) return
  status.value = 'running'

  const retryMessage: Message = {
    role: 'ai',
    content: '',
    logs: [],
    files: [],
    timestamp: Date.now(),
    taskStatus: 'queued',
    retryOf: msg.taskId
  }
  messages.value.push(retryMessage)
  scrollToBottom()

  try {
    const payload: any = {}
    if (currentThreadId.value) {
      payload.thread_id = currentThreadId.value
    }
    if (activeSessionId.value) {
      payload.session_id = activeSessionId.value
    }
    const res = await axios.post(`${API_BASE}/api/task/${msg.taskId}/retry`, payload)
    const taskId = res.data?.task_id
    const threadId = res.data?.thread_id
    const sessionId = res.data?.session_id
    if (threadId) {
      currentThreadId.value = threadId
    }
    if (sessionId) {
      activeSessionId.value = sessionId
      localStorage.setItem(SESSION_STORAGE_KEY, sessionId)
    }
    if (res.data?.session_dir) {
      currentSessionPath.value = res.data.session_dir
      const parts = currentSessionPath.value.split(/output[\\/]/)
      const rel = parts[1]
      if (rel) {
        currentSessionUrl.value = `${API_BASE}/outputs/${rel.replace(/\\/g, '/')}`
      }
    }
    if (!taskId) {
      throw new Error('Missing task_id in retry response')
    }
    appendLog(retryMessage, {
      type: 'info',
      title: `重试任务：${shortTaskId(taskId)}`,
      details: { retry_of: msg.taskId },
      timestamp: new Date().toLocaleTimeString()
    })
    startTaskTracking(taskId, retryMessage)
    await refreshSessions()
  } catch (error: any) {
    console.error('Retry task failed:', error)
    retryMessage.taskStatus = 'failed'
    retryMessage.taskError = error?.message || 'Retry request failed'
    retryMessage.content = `重试失败：${error?.response?.data ? JSON.stringify(error.response.data) : retryMessage.taskError}`
    status.value = 'idle'
  }
}

const getFooterTaskMessage = () => {
  if (activeTaskId.value) {
    const activeMsg = findAiMessageByTaskId(activeTaskId.value)
    if (activeMsg) return activeMsg
  }
  return messages.value
    .slice()
    .reverse()
    .find((m) => m.role === 'ai' && !!m.taskId)
}

const primaryAction = computed<'send' | 'stop' | 'retry'>(() => {
  const taskMsg = getFooterTaskMessage()
  if (taskMsg && canCancelTask(taskMsg)) {
    return 'stop'
  }
  if (!inputQuery.value.trim() && selectedFiles.value.length === 0 && taskMsg && canRetryTask(taskMsg)) {
    return 'retry'
  }
  return 'send'
})

const primaryActionDisabled = computed(() => {
  if (primaryAction.value === 'stop') return false
  if (primaryAction.value === 'retry') return false
  return !inputQuery.value.trim() && selectedFiles.value.length === 0
})

const primaryActionTitle = computed(() => {
  if (primaryAction.value === 'stop') return 'Stop'
  if (primaryAction.value === 'retry') return 'Retry'
  return 'Send'
})

const handlePrimaryAction = async () => {
  if (primaryAction.value === 'stop') {
    const taskMsg = getFooterTaskMessage()
    if (taskMsg) {
      await cancelTask(taskMsg)
    }
    return
  }
  if (primaryAction.value === 'retry') {
    const taskMsg = getFooterTaskMessage()
    if (taskMsg) {
      await retryTask(taskMsg)
    }
    return
  }
  await sendMessage()
}

// WebSocket Connection
const connectWebSocket = () => {
  if (!currentThreadId.value) return
  const ws = new WebSocket(`${WS_BASE}/ws/${currentThreadId.value}`)

  ws.onopen = () => {
    console.log('WebSocket Connected')
  }

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data)
      handleSocketMessage(data)
    } catch (e) {
      console.error('Error parsing WS message:', e)
    }
  }

  ws.onclose = () => {
    if (!shouldReconnectWs) return
    console.log('WebSocket Disconnected, retrying in 3s...')
    setTimeout(connectWebSocket, 3000)
  }

  socket.value = ws
}

// Handle Incoming Messages
const handleSocketMessage = (data: any) => {
  const { type, event, message, data: eventData } = data

  if (type === 'pong') return

  const lastAiMsg = findTrackedAiMessage()
  
  if (event === 'session_created') {
    currentSessionPath.value = eventData.path
    const parts = eventData.path.split(/output[\\/]/)
    if (parts.length > 1) {
      currentSessionUrl.value = `${API_BASE}/outputs/${parts[1].replace(/\\/g, '/')}`
    }
    isSidebarOpen.value = true
    fetchFiles()
  } else if (event === 'tool_start') {
    // 触发文件列表刷新，尽快展示新生成的文件
    if (currentSessionPath.value) {
      // 先立刻刷新一次，再延迟刷新一次，兼容工具异步落盘
      fetchFiles()
      setTimeout(fetchFiles, 2000)
    }

    if (lastAiMsg) {
      appendLog(lastAiMsg, {
        type: 'tool',
        title: `使用工具：${eventData.tool_name}...`,
        details: eventData.args,
        timestamp: new Date().toLocaleTimeString()
      })
      
      if (eventData.args && eventData.args.filename && currentSessionUrl.value) {
        if (!lastAiMsg.files) lastAiMsg.files = []
        const fileUrl = `${currentSessionUrl.value}/${eventData.args.filename}`
        // Avoid duplicates
        if (!lastAiMsg.files.find(f => f.name === eventData.args.filename)) {
           lastAiMsg.files.push({
            name: eventData.args.filename,
            path: eventData.args.filename,
            url: fileUrl
          })
        }
      }
    }
  } else if (event === 'assistant_call') {
    // 同样刷新文件列表
    if (currentSessionPath.value) {
        fetchFiles()
    }
     if (lastAiMsg) {
      appendLog(lastAiMsg, {
        type: 'agent',
        title: `正在调用助手：${eventData.assistant_name}...`,
        details: eventData.args,
        timestamp: new Date().toLocaleTimeString()
      })
    }
  } else if (event === 'task_result') {
    if (lastAiMsg) {
      lastAiMsg.content = eventData.result
      lastAiMsg.taskStatus = 'completed'
      lastAiMsg.taskError = undefined
    } else {
       messages.value.push({
        role: 'ai',
        content: eventData.result,
        taskStatus: 'completed',
        timestamp: Date.now()
      })
    }
    if (activeTaskId.value) {
      activeTaskId.value = null
      clearTaskPollTimer()
    }
    status.value = 'idle'
    fetchFiles()
    void refreshSessions()
  } else if (event === 'error') {
     if (lastAiMsg && lastAiMsg.taskStatus && !isTaskTerminal(lastAiMsg.taskStatus)) {
      lastAiMsg.taskStatus = 'failed'
      lastAiMsg.taskError = message
     }
     messages.value.push({
      role: 'system',
      content: `Error: ${message}`,
      timestamp: Date.now()
    })
    status.value = 'idle'
    void refreshSessions()
  }
  
  scrollToBottom()
}

// Send Message
const sendMessage = async () => {
  if ((!inputQuery.value.trim() && selectedFiles.value.length === 0) || status.value === 'running') return
  if (!activeSessionId.value) {
    await createNewSession()
  }

  const query = inputQuery.value
  inputQuery.value = ''
  status.value = 'running'

  messages.value.push({
    role: 'user',
    content: query,
    timestamp: Date.now()
  })

  messages.value.push({
    role: 'ai',
    content: '', // Start empty, show "Thinking" via logs/status if needed, or placeholder
    logs: [],
    files: [],
    taskStatus: 'queued',
    timestamp: Date.now()
  })

  scrollToBottom()

  // Handle File Upload
  if (selectedFiles.value.length > 0) {
    console.log('Uploading files:', selectedFiles.value)
    
    // Log to UI
    const lastAiMsg = messages.value[messages.value.length - 1]
    if (lastAiMsg && lastAiMsg.role === 'ai') {
        if (!lastAiMsg.logs) lastAiMsg.logs = []
        
        const fileDetails = selectedFiles.value.map(f => ({ name: f.name, size: f.size }))
        
        lastAiMsg.logs.push({
            type: 'info',
            title: `Uploading ${selectedFiles.value.length} file(s)...`,
            details: fileDetails,
            timestamp: new Date().toLocaleTimeString()
        })
    }

    // Actual Upload
    try {
        const formData = new FormData()
        // Ensure thread_id is available
        if (typeof currentThreadId !== 'undefined' && currentThreadId.value) {
             formData.append('thread_id', currentThreadId.value)
        } else {
             // Fallback if no thread ID (should ideally not happen as initialized in state)
             console.warn('No thread ID found for upload')
        }

        selectedFiles.value.forEach(file => {
            console.log(`Appending file to FormData: name=${file.name}, size=${file.size}, type=${file.type}`)
            formData.append('files', file)
        })

        await axios.post(`${API_BASE}/api/upload`, formData, {
            headers: {
                'Content-Type': 'multipart/form-data'
            }
        })
        
        // Clear files after successful upload
        selectedFiles.value = []
        
        if (lastAiMsg && lastAiMsg.logs) {
            lastAiMsg.logs.push({
                type: 'success',
                title: 'Files uploaded successfully',
                details: null,
                timestamp: new Date().toLocaleTimeString()
            })
        }

    } catch (e: any) {
        console.error('Upload failed', e)
        if (lastAiMsg && lastAiMsg.logs) {
            lastAiMsg.logs.push({
                type: 'error',
                title: 'File upload failed',
                details: e.message || 'Unknown error',
                timestamp: new Date().toLocaleTimeString()
            })
        }
        // Don't stop task execution, but maybe warn user?
    }
  }

  try {
    const payload: any = { query }
    if (activeSessionId.value) {
      payload.session_id = activeSessionId.value
    }
    // Only add thread_id if it exists and is not empty
    if (typeof currentThreadId !== 'undefined' && currentThreadId.value) {
      payload.thread_id = currentThreadId.value
    }
    console.log('Sending request payload:', payload)
    const res = await axios.post(`${API_BASE}/api/task`, payload)
    
    if (res.data && res.data.thread_id) {
      currentThreadId.value = res.data.thread_id
    }
    if (res.data && res.data.session_id) {
      activeSessionId.value = res.data.session_id
      localStorage.setItem(SESSION_STORAGE_KEY, res.data.session_id)
    }
    if (res.data && res.data.session_dir) {
      currentSessionPath.value = res.data.session_dir
      const parts = currentSessionPath.value.split(/output[\\/]/)
      const rel = parts[1]
      if (rel) {
        currentSessionUrl.value = `${API_BASE}/outputs/${rel.replace(/\\/g, '/')}`
      }
    }
    if (res.data && res.data.task_id) {
      const lastAiMsg = findLatestAiMessage()
      if (lastAiMsg && lastAiMsg.role === 'ai') {
        startTaskTracking(res.data.task_id, lastAiMsg)
      }
    }
    await refreshSessions()
  } catch (error: any) {
    console.error('Request failed:', error)
    let errorMsg = 'Failed to send request.'
    if (error.message) errorMsg += ` (${error.message})`
    if (error.response && error.response.data) {
        errorMsg += ` Server says: ${JSON.stringify(error.response.data)}`
    }
    
    messages.value.push({
      role: 'system',
      content: errorMsg,
      timestamp: Date.now()
    })
    const lastAiMsg = findLatestAiMessage()
    if (lastAiMsg && lastAiMsg.role === 'ai') {
      lastAiMsg.taskStatus = 'failed'
      lastAiMsg.taskError = error.message || 'Request failed'
      if (!lastAiMsg.content) {
        lastAiMsg.content = lastAiMsg.taskError || 'Request failed'
      }
    }
    status.value = 'idle'
  }
}

const triggerFileUpload = () => {
  fileInputRef.value?.click()
}

const handleFileChange = (event: Event) => {
  const target = event.target as HTMLInputElement
  if (target.files && target.files.length > 0) {
    // Append new files to existing list
    selectedFiles.value = [...selectedFiles.value, ...Array.from(target.files)]
    console.log('Files selected:', selectedFiles.value)
    // Reset input so same file can be selected again if needed
    target.value = ''
  }
}

const removeFile = (index: number) => {
  selectedFiles.value.splice(index, 1)
}

const renderMarkdown = (text: string) => {
  if (!text) return '<span class="typing-indicator">Thinking...</span>'
  return marked(text)
}

onMounted(() => {
  void bootstrapSessions()
})

onBeforeUnmount(() => {
  clearTaskPollTimer()
  shouldReconnectWs = false
  if (socket.value) {
    socket.value.close()
  }
})
</script>

<template>
  <div class="app-container">
    <aside class="sessions-sidebar">
      <div class="sessions-header">
        <h3>Chats</h3>
        <button class="new-chat-btn" @click="createNewSession" :disabled="status === 'running'">+ New</button>
      </div>
      <div class="sessions-list">
        <div
          v-for="session in sessions"
          :key="session.session_id"
          class="session-item-btn"
          :class="{ active: session.session_id === activeSessionId, disabled: status === 'running' }"
          @click="status !== 'running' && switchSession(session.session_id)"
        >
          <div class="session-row">
            <div class="session-title">{{ session.title || 'New Chat' }}</div>
            <button
              class="session-delete-btn"
              :disabled="status === 'running'"
              @click.stop="deleteSession(session.session_id)"
              title="Delete session"
            >
              ×
            </button>
          </div>
          <div class="session-preview">{{ session.last_message_preview || 'No messages yet' }}</div>
        </div>
      </div>
    </aside>

    <!-- Main Content -->
    <main class="main-content" :class="{ 'centered-layout': isWelcomeScreen }">
      
      <!-- Sidebar Toggle Button -->
      <button 
        v-if="currentSessionPath && !isSidebarOpen" 
        class="sidebar-toggle-btn" 
        @click="isSidebarOpen = true"
        title="Open File Sidebar"
      >
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
          <path d="M4 6H20M4 12H20M4 18H20" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </button>

      <!-- Welcome Screen -->
      <div v-if="isWelcomeScreen" class="welcome-screen">
        <div class="welcome-text">
          <h1>Hello, User</h1>
          <h2>How can I help you today?</h2>
        </div>
      </div>

      <!-- Chat Area -->
      <div v-else class="chat-scroll-area">
        <div class="chat-container">
          <div v-for="(msg, index) in messages" :key="index" class="message-wrapper" :class="msg.role">
            
            <!-- User Message -->
            <div v-if="msg.role === 'user'" class="message-user">
              <div class="msg-content">{{ msg.content }}</div>
            </div>

            <!-- AI Message -->
            <div v-else-if="msg.role === 'ai'" class="message-ai">
              <div class="ai-avatar">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                  <path d="M12 2L14.5 9.5L22 12L14.5 14.5L12 22L9.5 14.5L2 12L9.5 9.5L12 2Z" fill="url(#grad1)"/>
                  <defs>
                    <linearGradient id="grad1" x1="2" y1="2" x2="22" y2="22" gradientUnits="userSpaceOnUse">
                      <stop stop-color="#4E75F6"/>
                      <stop offset="1" stop-color="#E3557A"/>
                    </linearGradient>
                  </defs>
                </svg>
              </div>
              
              <div class="ai-content-wrapper">
                <!-- Logs / Thinking Process -->
                <div v-if="msg.logs && msg.logs.length > 0" class="process-section">
                  <details>
                    <summary>
                      <span class="spinner" v-if="status === 'running' && index === messages.length - 1"></span>
                      View thought process
                    </summary>
                    <div class="process-steps">
                      <div v-for="(log, idx) in msg.logs" :key="idx" class="step-item">
                        <div class="step-header">
                          <span class="step-icon">📌</span>
                          <span class="step-title">{{ log.title }}</span>
                        </div>
                        <div class="step-details" v-if="log.details">
                           <pre>{{ JSON.stringify(log.details, null, 2) }}</pre>
                        </div>
                      </div>
                    </div>
                  </details>
                </div>

                <!-- Text Content -->
                <div class="markdown-body" v-html="renderMarkdown(msg.content)"></div>

                <!-- Files -->
                <div v-if="msg.files && msg.files.length > 0" class="files-grid">
                  <a v-for="file in msg.files" :key="file.name" :href="file.url" target="_blank" class="file-card" :download="file.name">
                    <div class="file-icon">📄</div>
                    <div class="file-info">
                      <div class="file-name">{{ file.name }}</div>
                      <div class="file-type">Document</div>
                    </div>
                  </a>
                </div>
              </div>
            </div>

            <!-- System Message -->
             <div v-else class="message-system">
              {{ msg.content }}
            </div>

          </div>
          <div ref="messagesEndRef" class="spacer-bottom"></div>
        </div>
      </div>

      <!-- Input Area -->
      <footer class="input-footer">
        <!-- File Preview Tab -->
        <div v-if="selectedFiles.length > 0" class="file-preview-container">
          <div v-for="(file, index) in selectedFiles" :key="index" class="file-preview-chip">
            <span class="file-preview-icon">📎</span>
            <span class="file-preview-name">{{ file.name }}</span>
            <button class="file-remove-btn" @click="removeFile(index)" title="Remove file">×</button>
          </div>
        </div>

        <div class="input-container" :class="{ focused: status === 'running' }">
          <input 
            type="file" 
            ref="fileInputRef" 
            multiple
            style="display: none" 
            @change="handleFileChange" 
          />
          <button class="upload-btn" @click="triggerFileUpload" :disabled="status === 'running'" title="Upload file">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
          </button>
          <textarea 
            v-model="inputQuery" 
            @keydown.enter.exact.prevent="sendMessage"
            placeholder="Enter a prompt here"
            :disabled="status === 'running'"
          ></textarea>
          <button class="send-btn" @click="handlePrimaryAction" :disabled="primaryActionDisabled" :title="primaryActionTitle">
            <svg v-if="primaryAction === 'send'" viewBox="0 0 24 24" width="20" height="20" fill="currentColor">
              <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"></path>
            </svg>
            <svg v-else-if="primaryAction === 'stop'" width="20" height="20" viewBox="0 0 24 24" fill="none">
              <rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor" />
            </svg>
            <svg v-else width="20" height="20" viewBox="0 0 24 24" fill="none">
              <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
              <path d="M3 3v5h5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
          </button>
        </div>
        <div class="footer-text">
          DeepAgents may display inaccurate info, including about people, so double-check its responses.
        </div>
      </footer>
    </main>

    <!-- Right Sidebar (File Explorer) -->
    <aside v-if="isSidebarOpen" class="file-sidebar">
      <div class="sidebar-header">
        <h3>Session Files</h3>
        <div style="display: flex; gap: 8px; align-items: center;">
            <button class="folder-btn" @click="fetchFiles" title="Refresh Files" style="padding: 4px 8px;">
                ↻
            </button>
            <button class="close-btn" @click="isSidebarOpen = false">×</button>
        </div>
      </div>
      <div class="file-list">
        <div v-if="fileList.length === 0" class="empty-files">
          No files generated yet.
        </div>
        <div v-else v-for="file in fileList" :key="file.path" class="file-item">
          <a :href="file.url" target="_blank" class="file-link" :download="file.name">
            <span class="file-icon">📄</span>
            <span class="file-name-text">{{ file.name }}</span>
          </a>
        </div>
      </div>
    </aside>
  </div>
</template>

<style>
/* Global Resets & Variables */
:root {
  --bg-dark: #131314;
  --surface-dark: #1E1F20;
  --surface-light: #2D2E2F;
  --text-primary: #E3E3E3;
  --text-secondary: #C4C7C5;
  --accent-blue: #A8C7FA;
  --user-msg-bg: #2D2E30; /* Darker gray for user */
  --border-color: #444746;
}

body {
  margin: 0;
  background-color: var(--bg-dark);
  color: var(--text-primary);
  font-family: 'Google Sans', 'Roboto', Helvetica, Arial, sans-serif;
  overflow: hidden; /* App handles scroll */
}

/* Layout */
.app-container {
  display: flex;
  height: 100vh;
  width: 100vw;
  /* justify-content: center; Removed to allow sidebar layout */
}

/* Main Content */
.main-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  position: relative;
  background-color: var(--bg-dark);
  min-width: 0; /* Prevent flex overflow */
}

.sessions-sidebar {
  width: 260px;
  border-right: 1px solid var(--border-color);
  background: #171819;
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
}

.sessions-header {
  padding: 0.9rem 0.8rem;
  border-bottom: 1px solid var(--border-color);
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.sessions-header h3 {
  margin: 0;
  font-size: 0.95rem;
  font-weight: 600;
}

.new-chat-btn {
  border: 1px solid #4e4f50;
  background: transparent;
  color: var(--text-secondary);
  border-radius: 8px;
  padding: 0.3rem 0.55rem;
  cursor: pointer;
  font-size: 0.8rem;
}

.new-chat-btn:hover:not(:disabled) {
  border-color: #7b7d7f;
  color: var(--text-primary);
}

.new-chat-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.sessions-list {
  padding: 0.6rem;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
}

.session-item-btn {
  width: 100%;
  text-align: left;
  border: 1px solid transparent;
  background: #1f2022;
  color: var(--text-primary);
  border-radius: 10px;
  padding: 0.55rem 0.6rem;
  cursor: pointer;
}

.session-item-btn:hover:not(.disabled) {
  border-color: #4f5154;
}

.session-item-btn.active {
  border-color: #7a7d80;
  background: #25272a;
}

.session-item-btn.disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.session-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
}

.session-title {
  font-size: 0.86rem;
  font-weight: 600;
  margin-bottom: 0.2rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.session-delete-btn {
  border: 1px solid #4f5154;
  background: transparent;
  color: var(--text-secondary);
  border-radius: 6px;
  width: 22px;
  height: 22px;
  line-height: 1;
  cursor: pointer;
  padding: 0;
  flex-shrink: 0;
}

.session-delete-btn:hover:not(:disabled) {
  color: #ff8a80;
  border-color: #ff8a80;
}

.session-delete-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.session-preview {
  font-size: 0.74rem;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.sidebar-toggle-btn {
  position: absolute;
  top: 1rem;
  right: 1rem;
  background: transparent;
  border: none;
  color: var(--text-secondary);
  cursor: pointer;
  z-index: 10;
  padding: 8px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s;
}

.sidebar-toggle-btn:hover {
  background: rgba(255, 255, 255, 0.1);
  color: var(--text-primary);
}

/* File Sidebar */
.file-sidebar {
  width: 300px;
  background-color: var(--surface-dark);
  border-left: 1px solid var(--border-color);
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
}

.sidebar-header {
  padding: 1rem;
  border-bottom: 1px solid var(--border-color);
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.sidebar-header h3 {
  margin: 0;
  font-size: 1rem;
  font-weight: 500;
  color: var(--text-primary);
}

.close-btn {
  background: none;
  border: none;
  color: var(--text-secondary);
  font-size: 1.5rem;
  cursor: pointer;
  padding: 0;
  line-height: 1;
}

.close-btn:hover {
  color: var(--text-primary);
}

.file-list {
  flex: 1;
  overflow-y: auto;
  padding: 1rem;
}

.empty-files {
  color: var(--text-secondary);
  text-align: center;
  font-size: 0.9rem;
  margin-top: 2rem;
}

.file-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
}

.file-link {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.75rem;
  border-radius: 8px;
  color: var(--text-primary);
  text-decoration: none;
  transition: background 0.2s;
  border: 1px solid transparent;
}

.file-link:hover {
  background: #2D2E30;
  border-color: #444;
}

.folder-btn {
  background: transparent;
  border: 1px solid var(--border-color);
  color: var(--text-secondary);
  cursor: pointer;
  padding: 0.5rem;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s;
}

.folder-btn:hover {
  background: rgba(255, 255, 255, 0.1);
  color: var(--text-primary);
  border-color: var(--text-secondary);
}

.file-name-text {
  font-size: 0.9rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Welcome Screen */
.welcome-screen {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  padding: 2rem;
}

/* Centered Layout Mode (Initial State) */
.main-content.centered-layout {
  justify-content: center;
  align-items: center;
  overflow-y: auto;
}

.main-content.centered-layout .welcome-screen {
  flex: 0 0 auto;
  padding-bottom: 2rem;
}

.main-content.centered-layout .input-footer {
  width: 100%;
  max-width: 100%;
  padding: 0;
  background: transparent;
  justify-content: center;
}

.welcome-text {
  text-align: center;
  margin-bottom: 2rem;
}

.welcome-text h1 {
  font-size: 3.5rem;
  background: linear-gradient(90deg, #4E75F6, #E3557A);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  margin: 0;
  line-height: 1.2;
}

.welcome-text h2 {
  font-size: 3.5rem;
  color: #444746;
  margin: 0;
  line-height: 1.2;
}

/* Chat Area */
.chat-scroll-area {
  flex: 1;
  overflow-y: auto;
  padding: 1rem;
}

.chat-container {
  max-width: 800px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 2rem;
}

.message-wrapper {
  display: flex;
  flex-direction: column;
  width: 100%;
}

/* User Message */
.message-user {
  align-self: flex-end;
  max-width: 70%;
}

.msg-content {
  background-color: var(--user-msg-bg);
  padding: 12px 18px;
  border-radius: 18px;
  border-bottom-right-radius: 4px;
  line-height: 1.6;
}

/* AI Message */
.message-ai {
  align-self: flex-start;
  width: 100%;
  display: flex;
  gap: 1rem;
}

.ai-avatar {
  flex-shrink: 0;
  width: 28px;
  height: 28px;
  margin-top: 4px;
}

.ai-content-wrapper {
  flex: 1;
  min-width: 0; /* Text wrap fix */
}

.markdown-body {
  line-height: 1.6;
  font-size: 1rem;
}

.markdown-body pre {
  background: #2D2E30;
  padding: 1rem;
  border-radius: 8px;
  overflow-x: auto;
}

.typing-indicator {
  color: var(--text-secondary);
  font-style: italic;
  animation: pulse 1.5s infinite;
}

@keyframes pulse {
  0% { opacity: 0.5; }
  50% { opacity: 1; }
  100% { opacity: 0.5; }
}

/* Process / Logs */
.process-section {
  margin-bottom: 1rem;
}

.process-section summary {
  cursor: pointer;
  color: var(--text-secondary);
  font-size: 0.85rem;
  list-style: none; /* Hide default arrow */
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem;
  border-radius: 4px;
}

.process-section summary:hover {
  background: #2D2E30;
}

.spinner {
  width: 12px;
  height: 12px;
  border: 2px solid var(--text-secondary);
  border-top-color: transparent;
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

@keyframes spin { to { transform: rotate(360deg); } }

.process-steps {
  background: #1E1F20;
  border-radius: 8px;
  padding: 0.5rem;
  margin-top: 0.5rem;
  border: 1px solid #333;
}

.step-item {
  padding: 0.5rem;
  border-left: 2px solid #333;
  margin-left: 0.5rem;
  margin-bottom: 0.5rem;
}

.step-header {
  font-size: 0.85rem;
  font-weight: 500;
  color: #E3E3E3;
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.step-details pre {
  margin: 0.5rem 0 0 0;
  font-size: 0.75rem;
  color: #999;
  background: #111;
  padding: 0.5rem;
  border-radius: 4px;
  overflow-x: auto;
}

/* Files Grid */
.files-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
  margin-top: 1rem;
}

.file-card {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  background: #2D2E30;
  padding: 0.75rem 1rem;
  border-radius: 8px;
  text-decoration: none;
  color: var(--text-primary);
  border: 1px solid #444;
  transition: all 0.2s;
  min-width: 150px;
}

.file-card:hover {
  background: #333537;
  border-color: #666;
}

.file-info {
  display: flex;
  flex-direction: column;
}

.file-name {
  font-weight: 500;
  font-size: 0.9rem;
}

.file-type {
  font-size: 0.75rem;
  color: var(--text-secondary);
}

/* System Message */
.message-system {
  text-align: center;
  font-size: 0.8rem;
  color: #666;
  margin: 1rem 0;
}

.spacer-bottom { height: 100px; }

/* Input Footer */
.input-footer {
  background: var(--bg-dark); /* Ensure it covers scrolling content */
  padding: 1rem 2rem 2rem 2rem;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.75rem;
}

.input-container {
  width: 100%;
  max-width: 800px;
  background: #1E1F20;
  border-radius: 32px;
  display: flex;
  align-items: center;
  padding: 0.5rem 1rem;
  transition: background 0.2s;
}

.input-container.focused {
  background: #2D2E30;
}

textarea {
  flex: 1;
  background: transparent;
  border: none;
  color: var(--text-primary);
  font-size: 1rem;
  padding: 10px;
  resize: none;
  height: 24px;
  max-height: 200px;
  font-family: inherit;
  outline: none;
}

.send-btn {
  background: none;
  border: none;
  color: var(--text-primary); /* White when active */
  cursor: pointer;
  padding: 8px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
}

.send-btn:disabled {
  color: #444746;
  cursor: default;
}

.send-btn:not(:disabled):hover {
  background: #3c4043;
}

.upload-btn {
  background: none;
  border: none;
  color: var(--text-primary);
  cursor: pointer;
  padding: 8px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-right: 4px;
}

.upload-btn:hover {
  background: #3c4043;
}

.upload-btn:disabled {
  color: #444746;
  cursor: default;
}

.footer-text {
  font-size: 0.75rem;
  color: #444746;
  text-align: center;
}

/* File Preview Styles */
.file-preview-container {
  width: 100%;
  max-width: 800px;
  display: flex;
  justify-content: flex-start;
  padding-left: 1rem;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.file-preview-chip {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  background: #2D2E30;
  padding: 0.5rem 0.75rem;
  border-radius: 8px;
  border: 1px solid #444;
  font-size: 0.9rem;
  color: var(--text-primary);
  animation: slideUp 0.2s ease-out;
}

@keyframes slideUp {
  from { opacity: 0; transform: translateY(10px); }
  to { opacity: 1; transform: translateY(0); }
}

.file-preview-icon {
  font-size: 1rem;
}

.file-preview-name {
  max-width: 200px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.file-remove-btn {
  background: none;
  border: none;
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 1.1rem;
  padding: 0 4px;
  line-height: 1;
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.file-remove-btn:hover {
  background: rgba(255, 255, 255, 0.1);
  color: #ff6b6b;
}

/* Scrollbar Styles */
::-webkit-scrollbar {
  width: 8px;
  height: 8px;
}
::-webkit-scrollbar-track {
  background: transparent;
}
::-webkit-scrollbar-thumb {
  background: #444;
  border-radius: 4px;
}
::-webkit-scrollbar-thumb:hover {
  background: #555;
}
</style>

