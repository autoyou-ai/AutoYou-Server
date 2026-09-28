// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
// See LICENSE in the project root for license information.
// AI/ML training use prohibited without written authorization (License S3.9).
// AUTOYOU-PROVENANCE-P-7374726970652e636f6d2f65-7b751d5291754a8e615647a5

const express = require('express');
const crypto = require('node:crypto');
const { tunnelmole } = require('tunnelmole');
const WebSocket = require('ws');

class TunnelmoleClient {
    constructor() {
        this.app = express();
        this.server = null;
        this.tunnelmoleUrl = null;
        this.tunnelmoleInstance = null;
        this.port = process.env.TUNNELMOLE_PORT || 8022; // Port for tunnelmole client from environment
        this.sessionCache = new Map(); // Cache for active sessions (minimal data only)
        this.isRunning = false;
        this.wsServer = null; // WebSocket server for communication with server.py
        this.wsPort = process.env.TUNNELMOLE_WS_PORT || 8023; // WebSocket port for server communication
        this.serverConnection = null; // WebSocket connection to server.py
        this.pendingAuthRequests = new Map(); // Track pending auth requests
        
        this.setupMiddleware();
        this.setupRoutes();
        this.setupWebSocketServer();
    }

    setupMiddleware() {
        this.app.use(express.json());
        this.app.use(express.urlencoded({ extended: true }));
        
        // CORS middleware
        this.app.use((req, res, next) => {
            res.header('Access-Control-Allow-Origin', '*');
            res.header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
            res.header('Access-Control-Allow-Headers', 'Origin, X-Requested-With, Content-Type, Accept, Authorization');
            if (req.method === 'OPTIONS') {
                res.sendStatus(200);
            } else {
                next();
            }
        });
    }

    setupWebSocketServer() {
        // Create WebSocket server for communication with server.py
        this.wsServer = new WebSocket.Server({ port: this.wsPort });
        
        this.wsServer.on('connection', (ws) => {
            console.log('[Tunnelmole] WebSocket connection established with server.py');
            this.serverConnection = ws;
            
            ws.on('message', async (message) => {
                try {
                    const data = JSON.parse(message);
                    await this.handleServerMessage(data);
                } catch (error) {
                    console.error('[Tunnelmole] Error parsing WebSocket message:', error);
                    this.sendErrorToServer(ws, 'Invalid JSON message format');
                }
            });
            
            ws.on('close', () => {
                console.log('[Tunnelmole] WebSocket connection closed');
                this.serverConnection = null;
            });
            
            ws.on('error', (error) => {
                console.error('[Tunnelmole] WebSocket error:', error);
                this.handleWebSocketError(error);
            });
            
            // Send initial status
            this.sendStatusToServer(ws);
        });
        
        this.wsServer.on('error', (error) => {
            console.error('[Tunnelmole] WebSocket server error:', error);
            this.handleWebSocketError(error);
        });
        
        console.log(`[Tunnelmole] WebSocket server listening on port ${this.wsPort}`);
    }

    async handleServerMessage(data) {
        const { type, payload } = data;
        
        switch (type) {
            case 'auth_response':
                this.handleAuthResponse(payload);
                break;
            case 'start':
                this.handleStartRequest(payload);
                break;
            case 'shutdown':
                await this.handleShutdownRequest(payload);
                break;
            case 'webrtc_signal_response':
                this.handleWebRTCSignalResponse(payload);
                break;
            default:
                console.warn('[Tunnelmole] Unknown message type:', type);
                this.sendErrorToServer(this.serverConnection, `Unknown message type: ${type}`);
        }
    }

    handleAuthResponse(payload) {
        try {
            const { requestId, success, sessionId, iceServers, error } = payload;
            
            if (!this.pendingAuthRequests || !this.pendingAuthRequests.has(requestId)) {
                console.warn(`[Tunnelmole] Received auth response for unknown request: ${requestId}`);
                return;
            }
            
            const pendingReq = this.pendingAuthRequests.get(requestId);
            this.pendingAuthRequests.delete(requestId);
            
            if (pendingReq.res.headersSent) {
                console.warn(`[Tunnelmole] Response already sent for request: ${requestId}`);
                return;
            }
            
            if (success) {
                // Store session for WebRTC signaling (minimal data, no sensitive info)
                if (sessionId) {
                    this.sessionCache.set(sessionId, {
                        sessionId,
                        createdAt: Date.now(),
                        validated: true
                    });
                }
                
                pendingReq.res.json({
                    success: true,
                    session_id: sessionId,
                    sessionId,
                    iceServers: iceServers || [
                        { urls: ['stun:stun.l.google.com:19302'] },
                        { urls: ['stun:stun1.l.google.com:19302'] }
                    ]
                });
                
                console.log(`[Tunnelmole] Authentication successful for session: ${sessionId}`);
            } else {
                pendingReq.res.status(401).json({
                    success: false,
                    error: error || 'Authentication failed'
                });
                
                console.log(`[Tunnelmole] Authentication failed for request: ${requestId}`);
            }
            
        } catch (error) {
            console.error('[Tunnelmole] Error handling auth response:', error);
        }
    }

    handleStartRequest(payload) {
        try {
            console.log('[Tunnelmole] Received start request from server');
            
            // Start the tunnelmole service if not already running
            if (!this.isRunning) {
                this.start().then(() => {
                    // Send ready notification to server with public URL
                    this.sendReadyToServer(this.serverConnection);
                }).catch((error) => {
                    console.error('[Tunnelmole] Error starting service:', error);
                    this.sendErrorToServer(this.serverConnection, `Failed to start: ${error.message}`);
                });
            } else {
                console.log('[Tunnelmole] Service already running');
                this.sendReadyToServer(this.serverConnection);
            }
            
        } catch (error) {
            console.error('[Tunnelmole] Error handling start request:', error);
            this.sendErrorToServer(this.serverConnection, `Start request error: ${error.message}`);
        }
    }

    async handleShutdownRequest(payload) {
        try {
            const { reason, graceful = true } = payload;
            console.log(`[Tunnelmole] Received shutdown request: ${reason}`);
            
            if (graceful) {
                // Graceful shutdown - allow time for cleanup
                setTimeout(async () => {
                    await this.stop();
                    process.exit(0);
                }, 1000);
            } else {
                // Immediate shutdown
                await this.stop();
                process.exit(0);
            }
            
        } catch (error) {
            console.error('[Tunnelmole] Error handling shutdown request:', error);
            // Force exit on error
            process.exit(1);
        }
    }

    handleWebRTCSignalResponse(payload) {
        try {
            const { sessionId, signalType, signalData, requestId, success, error } = payload;
            console.log(`[Tunnelmole] Received WebRTC signal response for session: ${sessionId}, type: ${signalType}`);
            
            if (!this.sessionCache.has(sessionId)) {
                console.warn(`[Tunnelmole] Received response for unknown session: ${sessionId}`);
                return;
            }
            
            const session = this.sessionCache.get(sessionId);
            
            // Server-side trickle ICE candidate pushed asynchronously.
            if (signalType === 'candidate') {
                const candObj = (typeof signalData === 'object' && signalData) ? signalData : {};
                if (!session.pendingMessages) session.pendingMessages = [];
                if (candObj.candidate) {
                    session.pendingMessages.push({
                        type: 'candidate',
                        candidate: candObj.candidate,
                        sdpMid: candObj.sdpMid,
                        sdpMLineIndex: candObj.sdpMLineIndex
                    });
                }
                this.sessionCache.set(sessionId, session);
                return;
            }

            if (session.pendingResponses && session.pendingResponses.size > 0) {
                // Prefer explicit request correlation to avoid offer/candidate race mismatches.
                const key = (requestId && session.pendingResponses.has(requestId))
                    ? requestId
                    : session.pendingResponses.keys().next().value;
                const pendingResponse = key ? session.pendingResponses.get(key) : null;
                if (!pendingResponse) {
                    this.sessionCache.set(sessionId, session);
                    return;
                }
                session.pendingResponses.delete(key);
                clearTimeout(pendingResponse.timeout);

                let response;
                if (signalType === 'answer') {
                    // signalData is an object with {type: "answer", sdp: "sdp_string"}
                    // Extract the SDP string from the signalData object
                    const sdpString = typeof signalData === 'object' && signalData.sdp ? signalData.sdp : signalData;
                    response = {
                        success: true,
                        type: 'answer',
                        sdp: sdpString
                    };
                } else if (signalType === 'candidate_ack') {
                    const ok = !(typeof signalData === 'object' && signalData && signalData.success === false);
                    response = ok
                        ? { success: true, message: 'ICE candidate processed' }
                        : { success: false, error: (signalData && signalData.error) || 'ICE candidate failed' };
                } else if (success === false) {
                    response = {
                        success: false,
                        error: error || 'Signaling failed'
                    };
                } else {
                    response = {
                        success: true,
                        message: 'Signal processed'
                    };
                }

                pendingResponse.resolve(response);
                this.sessionCache.set(sessionId, session);
            }
            
        } catch (error) {
            console.error('[Tunnelmole] Error handling WebRTC signal response:', error);
        }
    }

    sendToServer(data) {
        if (this.serverConnection && this.serverConnection.readyState === WebSocket.OPEN) {
            this.serverConnection.send(JSON.stringify(data));
        } else {
            console.warn('[Tunnelmole] No active WebSocket connection to server');
        }
    }

    sendStatusToServer(ws) {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({
                type: 'status_update',
                payload: this.getStatus()
            }));
        }
    }

    sendReadyToServer(ws) {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({
                type: 'ready',
                payload: {
                    public_url: this.tunnelmoleUrl,
                    port: this.port,
                    isRunning: this.isRunning
                }
            }));
        }
    }

    sendErrorToServer(ws, errorMessage) {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({
                type: 'error',
                payload: { message: errorMessage }
            }));
        }
    }

    handleWebSocketError(error) {
        // Port error handling patterns from tunnelmole-service.js
        const errorMessage = error.message || error.toString();
        
        // Check for firewall/network connection errors
        if (errorMessage.includes('EACCES') || 
            errorMessage.includes('connect ECONNREFUSED') ||
            errorMessage.includes('tunnelmole-log-telemetry') ||
            errorMessage.includes('FetchError')) {
            console.warn('[Tunnelmole] Network/firewall issue detected - telemetry connection blocked');
            console.warn('[Tunnelmole] Attempting to continue tunnel setup without telemetry');
        }
        
        // Check for known limitation errors
        if (errorMessage.includes('limited to one tunnel') || 
            errorMessage.includes('anonymous user') ||
            errorMessage.includes('multiple tunnels') ||
            errorMessage.includes('tunnels per hour') ||
            errorMessage.includes('limited to 10 tunnels')) {
            if (errorMessage.includes('tunnels per hour')) {
                console.warn('[Tunnelmole] Hourly tunnel limit reached - anonymous users are limited to 10 tunnels per hour');
            } else {
                console.warn('[Tunnelmole] Tunnel limitation reached - anonymous users are limited to one tunnel at a time');
            }
            console.warn('[Tunnelmole] Server will continue running without tunnelmole');
        }
        
        // Send error to server if connection exists
        if (this.serverConnection) {
            this.sendErrorToServer(this.serverConnection, errorMessage);
        }
    }

    setupRoutes() {
        // Main auth endpoint for client authentication - forwards to server for validation
        this.app.post('/auth', (req, res) => {
            try {
                const { hash } = req.body;
                
                if (!hash) {
                    return res.status(400).json({
                        success: false,
                        error: 'Hash is required'
                    });
                }

                // Forward auth request to server via WebSocket
                if (!this.serverConnection || this.serverConnection.readyState !== WebSocket.OPEN) {
                    return res.status(503).json({
                        success: false,
                        error: 'Server connection not available'
                    });
                }

                // Generate request ID for tracking response
                const requestId = crypto.randomUUID();
                
                // Store pending request to match with response
                if (!this.pendingAuthRequests) {
                    this.pendingAuthRequests = new Map();
                }
                
                this.pendingAuthRequests.set(requestId, {
                    res,
                    timestamp: Date.now(),
                    hash
                });

                // Send auth request to server
                this.sendToServer({
                    type: 'auth_request',
                    payload: {
                        requestId,
                        hash,
                        tunnelmole_url: this.tunnelmoleUrl
                    }
                });

                console.log(`[Tunnelmole] Forwarded auth request to server: ${requestId}`);

                // Set timeout for auth request (30 seconds)
                setTimeout(() => {
                    if (this.pendingAuthRequests && this.pendingAuthRequests.has(requestId)) {
                        const pendingReq = this.pendingAuthRequests.get(requestId);
                        this.pendingAuthRequests.delete(requestId);
                        
                        if (!pendingReq.res.headersSent) {
                            pendingReq.res.status(408).json({
                                success: false,
                                error: 'Authentication request timeout'
                            });
                        }
                    }
                }, 30000);

            } catch (error) {
                console.error('[Tunnelmole] Auth error:', error);
                res.status(500).json({
                    success: false,
                    error: 'Internal server error'
                });
            }
        });

        // WebRTC signaling endpoint - relay to server.py
        this.app.post('/signal/:sessionId', async (req, res) => {
            try {
                const { sessionId } = req.params;
                const { type, sdp, candidate } = req.body;

                if (!this.sessionCache.has(sessionId)) {
                    return res.status(404).json({
                        success: false,
                        error: 'Session not found'
                    });
                }

                const session = this.sessionCache.get(sessionId);
                
                // Verify session is validated (no sensitive data check needed)
                if (!session.validated) {
                    return res.status(401).json({
                        success: false,
                        error: 'Session not validated'
                    });
                }

                // Relay WebRTC signaling message to server.py via WebSocket
                if (!this.serverConnection || this.serverConnection.readyState !== WebSocket.OPEN) {
                    return res.status(503).json({
                        success: false,
                        error: 'Server connection not available'
                    });
                }

                const signalingMessage = {
                    type: 'webrtc_signal',
                    payload: {
                        requestId: crypto.randomUUID(),
                        sessionId: sessionId,
                        signalType: type,
                        signalData: (() => {
                            if (type === 'offer') {
                                return req.body;
                            }
                            if (type === 'candidate') {
                                if (typeof candidate === 'object' && candidate !== null) {
                                    return candidate;
                                }
                                return {
                                    candidate: candidate || sdp,
                                    sdpMid: req.body.sdpMid,
                                    sdpMLineIndex: req.body.sdpMLineIndex
                                };
                            }
                            return req.body;
                        })()
                    }
                };

                console.log(`[Tunnelmole] Relaying WebRTC ${type} for session: ${sessionId}`);
                
                // Send to server.py and wait for response
                this.serverConnection.send(JSON.stringify(signalingMessage));
                
                // Store response handler for this session
                if (!session.pendingResponses) session.pendingResponses = new Map();
                const requestId = signalingMessage.payload.requestId;
                
                // Create a promise to wait for the response
                const responsePromise = new Promise((resolve, reject) => {
                    const timeout = setTimeout(() => {
                        if (session.pendingResponses) session.pendingResponses.delete(requestId);
                        reject(new Error('Signaling timeout'));
                    }, 10000); // 10 second timeout
                    
                    session.pendingResponses.set(requestId, {
                        resolve,
                        reject,
                        timeout
                    });
                });

                try {
                    const response = await responsePromise;
                    res.json(response);
                } catch (error) {
                    console.error(`[Tunnelmole] Signaling timeout for session ${sessionId}:`, error);
                    res.status(408).json({
                        success: false,
                        error: 'Signaling timeout'
                    });
                }

                // Update session
                this.sessionCache.set(sessionId, session);

            } catch (error) {
                console.error('[Tunnelmole] Signaling error:', error);
                res.status(500).json({
                    success: false,
                    error: 'Internal server error'
                });
            }
        });

        // Get signaling messages for polling - simplified version
        this.app.get('/signal/:sessionId', (req, res) => {
            try {
                const { sessionId } = req.params;

                if (!this.sessionCache.has(sessionId)) {
                    return res.status(404).json({
                        success: false,
                        error: 'Session not found'
                    });
                }

                const session = this.sessionCache.get(sessionId);
                
                // Verify session is validated
                if (!session.validated) {
                    return res.status(401).json({
                        success: false,
                        error: 'Session not validated'
                    });
                }

                // Return any pending messages stored locally (for compatibility)
                const messages = session.pendingMessages || [];
                session.pendingMessages = []; // Clear after sending
                
                this.sessionCache.set(sessionId, session);

                res.json({
                    success: true,
                    messages
                });

            } catch (error) {
                console.error('[Tunnelmole] Get signaling error:', error);
                res.status(500).json({
                    success: false,
                    error: 'Internal server error'
                });
            }
        });

        // Deny any non-pairing route on the public tunnel surface.
        this.app.use((req, res) => {
            res.status(404).json({
                success: false,
                error: 'Not Found'
            });
        });
    }

    // Method to generate session ID for WebRTC connections
    generateSessionId() {
        return crypto.randomUUID();
    }

    async start() {
        try {
            if (this.isRunning) {
                console.log('[Tunnelmole] Already running');
                return { success: true, url: this.tunnelmoleUrl };
            }

            // Start Express server
            this.server = this.app.listen(this.port, () => {
                console.log(`[Tunnelmole] Local server started on port ${this.port}`);
            });

            // Start Tunnelmole tunnel with timeout and error handling
            console.log('[Tunnelmole] Starting Tunnelmole tunnel...');
            
            // Set timeout for tunnel establishment
            const tunnelPromise = tunnelmole({
                port: this.port
            });
            
            try {
                this.tunnelmoleUrl = await Promise.race([
                    tunnelPromise,
                    new Promise((_, reject) => 
                        setTimeout(() => reject(new Error('Tunnel establishment timeout')), 30000)
                    )
                ]);
                
                this.isRunning = true;
                console.log(`[Tunnelmole] Public URL: ${this.tunnelmoleUrl}`);

                return {
                    success: true,
                    url: this.tunnelmoleUrl,
                    port: this.port
                };
                
            } catch (tunnelError) {
                // Handle tunnel-specific errors with patterns from tunnelmole-service.js
                const errorMessage = tunnelError.message || tunnelError.toString();
                
                if (errorMessage.includes('timeout')) {
                    console.warn('[Tunnelmole] Timeout waiting for tunnel URL');
                    console.warn('[Tunnelmole] Server will continue running without tunnelmole');
                } else if (errorMessage.includes('limited to one tunnel') || 
                          errorMessage.includes('anonymous user') ||
                          errorMessage.includes('multiple tunnels') ||
                          errorMessage.includes('tunnels per hour') ||
                          errorMessage.includes('limited to 10 tunnels')) {
                    if (errorMessage.includes('tunnels per hour')) {
                        console.warn('[Tunnelmole] Hourly tunnel limit reached - anonymous users are limited to 10 tunnels per hour');
                    } else {
                        console.warn('[Tunnelmole] Tunnel limitation reached - anonymous users are limited to one tunnel at a time');
                    }
                    console.warn('[Tunnelmole] Server will continue running without tunnelmole');
                } else if (errorMessage.includes('EACCES') || 
                          errorMessage.includes('connect ECONNREFUSED') ||
                          errorMessage.includes('tunnelmole-log-telemetry') ||
                          errorMessage.includes('FetchError')) {
                    console.warn('[Tunnelmole] Network/firewall issue detected - telemetry connection blocked');
                    console.warn('[Tunnelmole] Attempting to continue tunnel setup without telemetry');
                } else {
                    console.error('[Tunnelmole] Failed to establish tunnel:', errorMessage);
                }
                
                // Send error to server if WebSocket connection exists
                if (this.serverConnection) {
                    this.sendErrorToServer(this.serverConnection, errorMessage);
                }
                
                this.isRunning = false;
                return {
                    success: false,
                    error: errorMessage
                };
            }

        } catch (error) {
            console.error('[Tunnelmole] Start error:', error);
            this.isRunning = false;
            
            // Handle process startup errors
            const errorMessage = error.message || error.toString();
            if (errorMessage.includes('EADDRINUSE')) {
                console.error(`[Tunnelmole] Port ${this.port} is already in use`);
            } else if (errorMessage.includes('EACCES')) {
                console.error('[Tunnelmole] Permission denied - check firewall settings');
            }
            
            return {
                success: false,
                error: errorMessage
            };
        }
    }

    async stop() {
        try {
            console.log('[Tunnelmole] Stopping services...');
            
            // Close WebSocket server
            if (this.wsServer) {
                console.log('[Tunnelmole] Closing WebSocket server...');
                await new Promise((resolve) => {
                    this.wsServer.close((err) => {
                        if (err) {
                            console.error('[Tunnelmole] Error closing WebSocket server:', err.message);
                        } else {
                            console.log('[Tunnelmole] WebSocket server closed');
                        }
                        resolve();
                    });
                });
                this.wsServer = null;
            }
            
            // Close server connection if exists
            if (this.serverConnection) {
                try {
                    this.serverConnection.close();
                } catch (error) {
                    console.error('[Tunnelmole] Error closing server connection:', error.message);
                }
                this.serverConnection = null;
            }

            // Stop Express server
            if (this.server) {
                console.log('[Tunnelmole] Stopping Express server...');
                await new Promise((resolve) => {
                    this.server.close((err) => {
                        if (err) {
                            console.error('[Tunnelmole] Error stopping Express server:', err.message);
                        } else {
                            console.log('[Tunnelmole] Express server stopped');
                        }
                        resolve();
                    });
                });
                this.server = null;
            }

            // Clear state
            this.isRunning = false;
            this.tunnelmoleUrl = null;
            this.sessionCache.clear();
            this.pendingAuthRequests.clear();

            console.log('[Tunnelmole] All services stopped');
            return { success: true };

        } catch (error) {
            console.error('[Tunnelmole] Stop error:', error.message);
            
            // Force cleanup even if errors occurred
            this.isRunning = false;
            this.tunnelmoleUrl = null;
            this.server = null;
            this.wsServer = null;
            this.serverConnection = null;
            this.sessionCache.clear();
            this.pendingAuthRequests.clear();
            
            return {
                success: false,
                error: error.message
            };
        }
    }

    getStatus() {
        return {
            isRunning: this.isRunning,
            tunnelmoleUrl: this.tunnelmoleUrl,
            port: this.port,
            activeSessions: this.sessionCache.size,
            pendingAuthRequests: this.pendingAuthRequests.size
        };
    }
}

// Export for use as module
module.exports = TunnelmoleClient;

// If run directly, start the server
if (require.main === module) {
    const client = new TunnelmoleClient();
    
    // Handle graceful shutdown
    process.on('SIGINT', async () => {
        console.log('\n[Tunnelmole] Received SIGINT, shutting down gracefully...');
        await client.stop();
        process.exit(0);
    });

    process.on('SIGTERM', async () => {
        console.log('\n[Tunnelmole] Received SIGTERM, shutting down gracefully...');
        await client.stop();
        process.exit(0);
    });

    // Start the client
    client.start().then(result => {
        if (result.success) {
            console.log(`[Tunnelmole] Started successfully: ${result.url}`);
        } else {
            console.error(`[Tunnelmole] Failed to start: ${result.error}`);
            process.exit(1);
        }
    });
}
