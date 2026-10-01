import { LightningElement, api, track, wire } from 'lwc';
import { ShowToastEvent } from 'lightning/platformShowToastEvent';
import { refreshApex } from '@salesforce/apex';
import ask from '@salesforce/apex/ClaudeTraceController.ask';
import poll from '@salesforce/apex/ClaudeTraceController.poll';
import reportIssue from '@salesforce/apex/ClaudeTraceController.reportIssue';
import recent from '@salesforce/apex/ClaudeTraceController.recent';

const POLL_MS = 5000;
const MAX_POLL_MINUTES = 20;

export default class ClaudeAsk extends LightningElement {
    @api recordId;
    @api objectApiName;
    @api title = 'Ask Claude';
    @api placeholder = 'Describe what happened.';

    question = '';
    comment = '';
    busy = false;
    errorMessage = '';
    showReportForm = false;
    showHistory = false;
    startedAt = null;
    elapsedLabel = '';
    @track current = null;
    @track history = [];

    _timer = null;
    _tick = null;
    _wiredRecent;

    @wire(recent, { recordId: '$recordId', max: 10 })
    wiredRecent(result) {
        this._wiredRecent = result;
        if (result.data) {
            this.history = result.data;
            // Resume a running investigation if the user comes back to the page.
            if (!this.current) {
                const running = result.data.find(r => r.status === 'Queued' || r.status === 'Running');
                if (running) {
                    this.current = running;
                    this.startedAt = new Date(running.createdDate).getTime();
                    this.startPolling();
                }
            }
        }
    }

    disconnectedCallback() {
        this.stopPolling();
    }

    // ---------------------------------------------------------- state
    get showAskForm() { return !this.current; }
    get isRunning() { return this.current && (this.current.status === 'Queued' || this.current.status === 'Running'); }
    get isAnswered() { return this.current && (this.current.status === 'Answered' || this.current.status === 'Reported'); }
    get isReported() { return this.current && this.current.status === 'Reported' && this.current.issueUrl; }
    get canReport() { return this.current && this.current.status === 'Answered'; }
    get isFailed() { return this.current && this.current.status === 'Failed'; }
    get askDisabled() { return this.busy || !this.question || this.question.trim().length < 8; }
    get historyLabel() { return (this.showHistory ? 'Hide' : 'Show') + ' previous questions (' + this.history.length + ')'; }
    get confidenceLabel() { return (this.current && this.current.confidence ? this.current.confidence : 'Unknown') + ' confidence'; }
    get confidenceClass() {
        const c = this.current && this.current.confidence;
        return 'slds-m-left_x-small badge-' + (c === 'High' ? 'high' : c === 'Medium' ? 'medium' : 'low');
    }
    get fixLabel() {
        const s = this.current && this.current.fixStatus;
        if (!s) return '';
        return {
            awaiting_approval: 'Engineering is reviewing whether Claude should fix it.',
            approved: 'The fix was approved and is being prepared.',
            running: 'Claude is working on the fix.',
            pr_open: 'A fix is ready for engineering review.',
            merged: 'The fix has been merged.',
            failed: 'The automatic fix did not work; engineering will handle it.',
            declined: 'Engineering decided not to change the code for this.'
        }[s] || '';
    }

    // ---------------------------------------------------------- handlers
    handleQuestion(e) { this.question = e.target.value; }
    handleComment(e) { this.comment = e.target.value; }
    toggleReportForm() { this.showReportForm = !this.showReportForm; }
    toggleHistory() { this.showHistory = !this.showHistory; }

    async handleAsk() {
        this.errorMessage = '';
        this.busy = true;
        try {
            this.current = await ask({ question: this.question, recordId: this.recordId, objectApiName: this.objectApiName });
            this.question = '';
            this.startedAt = Date.now();
            this.startPolling();
            this.refreshHistory();
        } catch (e) {
            this.errorMessage = this.describe(e);
        } finally {
            this.busy = false;
        }
    }

    async handleReport() {
        this.errorMessage = '';
        this.busy = true;
        try {
            this.current = await reportIssue({ investigationId: this.current.investigationId, comment: this.comment });
            this.comment = '';
            this.showReportForm = false;
            this.dispatchEvent(new ShowToastEvent({ title: 'Reported to engineering', message: 'Issue #' + this.current.issueNumber + ' was created.', variant: 'success' }));
            this.refreshHistory();
            this.startPolling(); // keep watching for the fix status
        } catch (e) {
            this.errorMessage = this.describe(e);
        } finally {
            this.busy = false;
        }
    }

    handleNew() {
        this.stopPolling();
        this.current = null;
        this.showReportForm = false;
        this.errorMessage = '';
    }

    handleRetry() {
        this.question = this.current ? this.current.question : '';
        this.handleNew();
    }

    handleOpenHistory(e) {
        const id = e.currentTarget.dataset.id;
        const item = this.history.find(h => h.investigationId === id);
        if (!item) return;
        this.stopPolling();
        this.current = item;
        this.showHistory = false;
        if (item.status === 'Queued' || item.status === 'Running' || (item.status === 'Reported' && !item.fixPrUrl)) {
            this.startedAt = new Date(item.createdDate).getTime();
            this.startPolling();
        }
    }

    // ---------------------------------------------------------- polling
    startPolling() {
        this.stopPolling();
        this.updateElapsed();
        this._tick = setInterval(() => this.updateElapsed(), 1000);
        this._timer = setInterval(() => this.pollOnce(), POLL_MS);
        this.pollOnce();
    }

    stopPolling() {
        if (this._timer) clearInterval(this._timer);
        if (this._tick) clearInterval(this._tick);
        this._timer = null;
        this._tick = null;
    }

    async pollOnce() {
        if (!this.current) return this.stopPolling();
        try {
            const r = await poll({ investigationId: this.current.investigationId });
            const wasRunning = this.isRunning;
            this.current = r;
            if (wasRunning && (r.status === 'Answered' || r.status === 'Failed')) {
                this.dispatchEvent(new ShowToastEvent({
                    title: r.status === 'Answered' ? 'Claude has an answer' : 'Claude could not finish',
                    message: r.status === 'Answered' ? 'See the answer on this page.' : r.error,
                    variant: r.status === 'Answered' ? 'success' : 'warning'
                }));
                this.refreshHistory();
            }
            const stillWatching = this.isRunning || (r.status === 'Reported' && r.fixStatus && !['pr_open', 'merged', 'declined', 'failed'].includes(r.fixStatus));
            if (!stillWatching) this.stopPolling();
            if (this.isRunning && Date.now() - this.startedAt > MAX_POLL_MINUTES * 60000) {
                this.stopPolling();
                this.errorMessage = 'Still working after ' + MAX_POLL_MINUTES + ' minutes. The answer will be saved on ' + r.name + ' when it is ready.';
            }
        } catch (e) {
            this.errorMessage = this.describe(e);
        }
    }

    updateElapsed() {
        if (!this.startedAt) return;
        const s = Math.max(0, Math.floor((Date.now() - this.startedAt) / 1000));
        this.elapsedLabel = (s < 60 ? s + 's' : Math.floor(s / 60) + 'm ' + (s % 60) + 's') + ' elapsed';
    }

    refreshHistory() {
        if (this._wiredRecent) refreshApex(this._wiredRecent);
    }

    describe(e) {
        if (!e) return 'Something went wrong.';
        if (e.body && e.body.message) return e.body.message;
        if (Array.isArray(e.body)) return e.body.map(b => b.message).join(', ');
        return e.message || String(e);
    }
}
