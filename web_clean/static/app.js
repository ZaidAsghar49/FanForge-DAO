document.addEventListener("DOMContentLoaded", () => {
    // State management
    let currentPage = 1;
    let totalPages = 1;
    const itemsPerPage = 10;
    let activeInningsIndex = 0;
    let runsChart = null;

    // DOM Elements
    const tabButtons = document.querySelectorAll(".nav-btn");
    const tabPanes = document.querySelectorAll(".tab-pane");
    
    // Stats elements
    const statMatches = document.getElementById("stat-matches");
    const statDeliveries = document.getElementById("stat-deliveries");
    const statPlayers = document.getElementById("stat-players");
    const statRange = document.getElementById("stat-range");

    // Match browser filters & pagination
    const filterFormat = document.getElementById("filter-format");
    const filterYear = document.getElementById("filter-year");
    const filterTeam = document.getElementById("filter-team");
    const searchMatches = document.getElementById("search-matches");
    const matchesContainer = document.getElementById("matches-container");
    const btnPrev = document.getElementById("btn-prev");
    const btnNext = document.getElementById("btn-next");
    const pageInfo = document.getElementById("page-info");

    // Match Detail Modal
    const matchModal = document.getElementById("match-modal");
    const btnCloseModal = document.getElementById("btn-close-modal");
    const modalFormat = document.getElementById("modal-format");
    const modalTitle = document.getElementById("modal-title");
    const modalMeta = document.getElementById("modal-meta");
    const modalToss = document.getElementById("modal-toss");
    const modalPom = document.getElementById("modal-pom");
    const modalOutcome = document.getElementById("modal-outcome");
    const scorecardTabsContainer = document.getElementById("scorecard-tabs-container");
    const scorecardContentContainer = document.getElementById("scorecard-content-container");

    // Player search & analytics
    const playerSearchInput = document.getElementById("player-search-input");
    const playerSuggestions = document.getElementById("player-suggestions");
    const btnAnalyzePlayer = document.getElementById("btn-analyze-player");
    const playerProfile = document.getElementById("player-profile");
    const playerEmptyState = document.getElementById("player-empty-state");
    const profileName = document.getElementById("profile-name");
    const profileBattingBody = document.getElementById("profile-batting-body");
    const profileBowlingBody = document.getElementById("profile-bowling-body");

    // SQL Playground
    const sqlInput = document.getElementById("sql-input");
    const btnRunSql = document.getElementById("btn-run-sql");
    const sqlRowCount = document.getElementById("sql-row-count");
    const sqlTableContainer = document.getElementById("sql-table-container");
    const presetBtns = document.querySelectorAll(".preset-btn");

    // Initialize Page
    init();

    function init() {
        setupTabNavigation();
        fetchGlobalStats();
        fetchMatchesList();
        setupFilters();
        setupModal();
        setupPlayerAutocomplete();
        setupSQLPlayground();
    }

    // --- Tab Navigation ---
    function setupTabNavigation() {
        tabButtons.forEach(btn => {
            btn.addEventListener("click", () => {
                const targetTabId = btn.getAttribute("data-tab");
                
                // Toggle active buttons
                tabButtons.forEach(b => b.classList.remove("active"));
                btn.classList.add("active");

                // Toggle active content panes
                tabPanes.forEach(pane => {
                    if (pane.id === targetTabId) {
                        pane.classList.add("active");
                    } else {
                        pane.classList.remove("active");
                    }
                });
            });
        });
    }

    // --- Fetch Global Stats & Populate Dropdowns ---
    async function fetchGlobalStats() {
        try {
            const res = await fetch("/api/stats");
            const data = await res.json();
            
            // Populate metrics
            statMatches.textContent = data.total_matches.toLocaleString();
            statDeliveries.textContent = data.total_deliveries.toLocaleString();
            statPlayers.textContent = data.total_players.toLocaleString();
            statRange.textContent = `${data.date_range.min.substring(0, 4)} - ${data.date_range.max.substring(0, 4)}`;

            // Populate Team dropdown filter
            filterTeam.innerHTML = '<option value="">All Teams</option>';
            data.teams.forEach(team => {
                const opt = document.createElement("option");
                opt.value = team;
                opt.textContent = team;
                filterTeam.appendChild(opt);
            });
        } catch (err) {
            console.error("Error fetching global database stats:", err);
        }
    }

    // --- Fetch Matches Browser List ---
    async function fetchMatchesList() {
        // Show loading spinner
        matchesContainer.innerHTML = `
            <div class="spinner-container">
                <div class="spinner"></div>
                <p>Loading clean matches...</p>
            </div>
        `;

        const format = filterFormat.value;
        const year = filterYear.value;
        const team = filterTeam.value;
        const search = searchMatches.value;

        // Build query string
        let url = `/api/matches?page=${currentPage}&limit=${itemsPerPage}`;
        if (format) url += `&format=${format}`;
        if (year) url += `&year=${year}`;
        if (team) url += `&team=${encodeURIComponent(team)}`;
        if (search) url += `&search=${encodeURIComponent(search)}`;

        try {
            const res = await fetch(url);
            const data = await res.json();

            totalPages = data.total_pages || 1;
            pageInfo.textContent = `Page ${currentPage} of ${totalPages}`;
            
            btnPrev.disabled = currentPage <= 1;
            btnNext.disabled = currentPage >= totalPages;

            renderMatches(data.matches);
        } catch (err) {
            console.error("Error fetching match grid:", err);
            matchesContainer.innerHTML = `<p class="error-msg">Error loading matches. Ensure database file is active.</p>`;
        }
    }

    function renderMatches(matches) {
        if (!matches || matches.length === 0) {
            matchesContainer.innerHTML = `
                <div class="empty-state">
                    <i class="fa-solid fa-face-frown"></i>
                    <h3>No Matches Found</h3>
                    <p>No clean international matches match your current filter criteria.</p>
                </div>
            `;
            return;
        }

        matchesContainer.innerHTML = "";
        matches.forEach(match => {
            const card = document.createElement("div");
            card.className = "match-card";
            card.innerHTML = `
                <div class="match-card-header">
                    <span class="format-badge ${match.match_format}">${match.match_format}</span>
                    <span>${match.date} | ${match.city || match.venue}</span>
                </div>
                <div class="match-card-teams">
                    <div class="team-display">
                        <span class="name">${match.team1}</span>
                    </div>
                    <span class="vs-divider">vs</span>
                    <div class="team-display right">
                        <span class="name">${match.team2}</span>
                    </div>
                </div>
                <div class="match-card-footer">
                    <span>${match.venue}</span>
                    <span class="result">${match.result_winner ? match.result_winner + ' won' : 'Draw'}</span>
                </div>
            `;
            card.addEventListener("click", () => showMatchDetail(match.match_id));
            matchesContainer.appendChild(card);
        });
    }

    // --- Filters Setup ---
    function setupFilters() {
        const triggerSearch = debounce(() => {
            currentPage = 1;
            fetchMatchesList();
        }, 300);

        filterFormat.addEventListener("change", () => { currentPage = 1; fetchMatchesList(); });
        filterYear.addEventListener("change", () => { currentPage = 1; fetchMatchesList(); });
        filterTeam.addEventListener("change", () => { currentPage = 1; fetchMatchesList(); });
        searchMatches.addEventListener("input", triggerSearch);

        btnPrev.addEventListener("click", () => {
            if (currentPage > 1) {
                currentPage--;
                fetchMatchesList();
            }
        });

        btnNext.addEventListener("click", () => {
            if (currentPage < totalPages) {
                currentPage++;
                fetchMatchesList();
            }
        });
    }

    // --- Modal Overlay & Scorecard Renderer ---
    function setupModal() {
        btnCloseModal.addEventListener("click", () => {
            matchModal.classList.add("hidden");
        });

        // Close on clicking outside card
        matchModal.addEventListener("click", (e) => {
            if (e.target === matchModal) {
                matchModal.classList.add("hidden");
            }
        });
    }

    async function showMatchDetail(matchId) {
        matchModal.classList.remove("hidden");
        
        scorecardTabsContainer.innerHTML = "";
        scorecardContentContainer.innerHTML = `
            <div class="spinner-container">
                <div class="spinner"></div>
                <p>Fetching scorecard statistics...</p>
            </div>
        `;

        try {
            const res = await fetch(`/api/match/${matchId}`);
            const data = await res.json();
            
            const info = data.match_info;
            modalFormat.className = `format-badge ${info.match_format}`;
            modalFormat.textContent = info.match_format;
            modalTitle.textContent = `${info.team1} vs ${info.team2}`;
            modalMeta.innerHTML = `<i class="fa-solid fa-map-pin"></i> ${info.venue}, ${info.city || ''} | ${info.date}`;
            
            modalToss.textContent = info.toss_winner ? `${info.toss_winner} (${info.toss_decision})` : 'N/A';
            modalPom.textContent = info.player_of_match || 'N/A';
            
            let resultText = "Draw";
            if (info.result_winner) {
                const margin = info.result_margin ? ` by ${info.result_margin} ${info.result_unit}` : '';
                resultText = `${info.result_winner} won${margin}`;
            } else if (info.result) {
                resultText = info.result;
            }
            modalOutcome.textContent = resultText;

            // Render scorecards (innings data)
            renderScorecardTabs(data.innings);
        } catch (err) {
            console.error("Error displaying scorecard details:", err);
            scorecardContentContainer.innerHTML = `<p class="error-msg">Failed to load scorecard detail.</p>`;
        }
    }

    function renderScorecardTabs(inningsList) {
        scorecardTabsContainer.innerHTML = "";
        if (!inningsList || inningsList.length === 0) {
            scorecardContentContainer.innerHTML = `<p class="error-msg">No innings data recorded for this match.</p>`;
            return;
        }

        inningsList.forEach((inns, idx) => {
            const tabBtn = document.createElement("button");
            tabBtn.className = `tab-indicator ${idx === 0 ? 'active' : ''}`;
            tabBtn.textContent = `${inns.team} - Inns ${inns.innings_number}`;
            tabBtn.addEventListener("click", () => {
                document.querySelectorAll(".tab-indicator").forEach(b => b.classList.remove("active"));
                tabBtn.classList.add("active");
                renderInningsScorecard(inns);
            });
            scorecardTabsContainer.appendChild(tabBtn);
        });

        // Load first innings default
        renderInningsScorecard(inningsList[0]);
    }

    function renderInningsScorecard(inns) {
        let battingHtml = "";
        let bowlingHtml = "";

        // Batting
        if (inns.batting && inns.batting.length > 0) {
            inns.batting.forEach(row => {
                let status = "not out";
                if (row.wicket_kind) {
                    const fielderText = row.fielder ? ` (c ${row.fielder})` : '';
                    if (row.wicket_kind === "bowled" || row.wicket_kind === "lbw") {
                        status = `${row.wicket_kind} b ${row.dismisser}`;
                    } else if (row.wicket_kind === "caught") {
                        status = `c ${row.fielder || 'fielder'} b ${row.dismisser}`;
                    } else if (row.wicket_kind === "run out") {
                        status = `run out ${fielderText}`;
                    } else {
                        status = `${row.wicket_kind} b ${row.dismisser}`;
                    }
                }
                
                const sr = row.balls > 0 ? ((row.runs / row.balls) * 100).toFixed(2) : '0.00';

                battingHtml += `
                    <tr>
                        <td><strong>${row.batter}</strong></td>
                        <td class="dism-col">${status}</td>
                        <td class="runs-col">${row.runs}</td>
                        <td>${row.balls}</td>
                        <td>${row.fours}</td>
                        <td>${row.sixes}</td>
                        <td class="sr-col">${sr}</td>
                    </tr>
                `;
            });
        } else {
            battingHtml = `<tr><td colspan="7" class="text-muted text-center">No batting statistics available</td></tr>`;
        }

        // Bowling
        if (inns.bowling && inns.bowling.length > 0) {
            inns.bowling.forEach(row => {
                const oversFloat = parseFloat(row.overs);
                const economy = oversFloat > 0 ? (row.runs_conceded / oversFloat).toFixed(2) : '0.00';
                
                bowlingHtml += `
                    <tr>
                        <td><strong>${row.bowler}</strong></td>
                        <td>${row.overs}</td>
                        <td class="runs-col">${row.runs_conceded}</td>
                        <td class="sr-col">${row.wickets}</td>
                        <td>${row.dot_balls}</td>
                        <td>${economy}</td>
                    </tr>
                `;
            });
        } else {
            bowlingHtml = `<tr><td colspan="6" class="text-muted text-center">No bowling statistics available</td></tr>`;
        }

        const totalWkts = inns.total_wickets !== undefined ? `${inns.total_wickets} wkts` : '';

        scorecardContentContainer.innerHTML = `
            <div class="scorecard-table-title">
                <span><i class="fa-solid fa-baseball-bat-ball"></i> Batting Card</span>
                <span class="total">Total: ${inns.total_runs || 0} (${totalWkts}) | Extras: ${inns.total_extras || 0}</span>
            </div>
            <div class="stats-table-wrapper">
                <table class="stats-table">
                    <thead>
                        <tr>
                            <th>Batter</th>
                            <th>Status</th>
                            <th>Runs</th>
                            <th>Balls</th>
                            <th>4s</th>
                            <th>6s</th>
                            <th>S/R</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${battingHtml}
                    </tbody>
                </table>
            </div>

            <div class="table-divider"></div>

            <div class="scorecard-table-title">
                <span><i class="fa-solid fa-circle-dot"></i> Bowling Card</span>
            </div>
            <div class="stats-table-wrapper">
                <table class="stats-table">
                    <thead>
                        <tr>
                            <th>Bowler</th>
                            <th>Overs</th>
                            <th>Runs</th>
                            <th>Wickets</th>
                            <th>Dots</th>
                            <th>Economy</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${bowlingHtml}
                    </tbody>
                </table>
            </div>
        `;
    }

    // --- Player Profile Search & Analytics ---
    function setupPlayerAutocomplete() {
        let suggestionTimeout = null;

        playerSearchInput.addEventListener("input", () => {
            clearTimeout(suggestionTimeout);
            const val = playerSearchInput.value.trim();
            if (val.length < 2) {
                playerSuggestions.style.display = "none";
                return;
            }

            suggestionTimeout = setTimeout(async () => {
                try {
                    const res = await fetch(`/api/player/search?q=${encodeURIComponent(val)}`);
                    const data = await res.json();
                    
                    if (data.players && data.players.length > 0) {
                        playerSuggestions.innerHTML = "";
                        data.players.forEach(p => {
                            const div = document.createElement("div");
                            div.className = "suggestion-item";
                            div.textContent = p;
                            div.addEventListener("click", () => {
                                playerSearchInput.value = p;
                                playerSuggestions.style.display = "none";
                                triggerPlayerProfile(p);
                            });
                            playerSuggestions.appendChild(div);
                        });
                        playerSuggestions.style.display = "block";
                    } else {
                        playerSuggestions.style.display = "none";
                    }
                } catch (err) {
                    console.error("Autocomplete failure:", err);
                }
            }, 200);
        });

        // Hide suggestions when clicking outside
        document.addEventListener("click", (e) => {
            if (e.target !== playerSearchInput) {
                playerSuggestions.style.display = "none";
            }
        });

        btnAnalyzePlayer.addEventListener("click", () => {
            const val = playerSearchInput.value.trim();
            if (val) triggerPlayerProfile(val);
        });
    }

    async function triggerPlayerProfile(playerName) {
        playerEmptyState.classList.add("hidden");
        playerProfile.classList.add("hidden");

        try {
            const res = await fetch(`/api/player/stats?player=${encodeURIComponent(playerName)}`);
            if (!res.ok) {
                throw new Error("Player not found");
            }
            const data = await res.json();
            
            profileName.textContent = data.player;
            renderPlayerProfileStats(data);
            renderPlayerHistoryChart(data.runs_history);

            playerProfile.classList.remove("hidden");
        } catch (err) {
            console.error("Error profiling player stats:", err);
            playerEmptyState.classList.remove("hidden");
            playerEmptyState.innerHTML = `
                <i class="fa-solid fa-face-frown-open" style="color: var(--accent-amber)"></i>
                <h3>Player Not Found</h3>
                <p>Could not fetch stats. Ensure you typed the name exactly as registered in Cricsheet database records.</p>
            `;
        }
    }

    function renderPlayerProfileStats(data) {
        // Render Batting summary rows
        profileBattingBody.innerHTML = "";
        const formats = ["Test", "ODI"];
        
        formats.forEach(fmt => {
            const stat = data.batting[fmt];
            if (stat) {
                const hsStr = stat.high_score_not_out ? `${stat.high_score}*` : `${stat.high_score}`;
                profileBattingBody.innerHTML += `
                    <tr>
                        <td><strong>${fmt}</strong></td>
                        <td>${stat.innings}</td>
                        <td class="runs-col">${stat.runs.toLocaleString()}</td>
                        <td>${stat.average}</td>
                        <td class="sr-col">${stat.strike_rate}</td>
                        <td>${hsStr}</td>
                        <td>${stat.hundreds}</td>
                        <td>${stat.fifties}</td>
                    </tr>
                `;
            } else {
                profileBattingBody.innerHTML += `
                    <tr>
                        <td><strong>${fmt}</strong></td>
                        <td colspan="7" class="text-muted text-center">No batting registered</td>
                    </tr>
                `;
            }
        });

        // Render Bowling summary rows
        profileBowlingBody.innerHTML = "";
        formats.forEach(fmt => {
            const stat = data.bowling[fmt];
            if (stat) {
                profileBowlingBody.innerHTML += `
                    <tr>
                        <td><strong>${fmt}</strong></td>
                        <td>${stat.matches}</td>
                        <td>${stat.overs}</td>
                        <td class="runs-col">${stat.wickets}</td>
                        <td>${stat.average}</td>
                        <td>${stat.economy}</td>
                        <td class="sr-col">${stat.best_bowling}</td>
                        <td>${stat.five_wickets}</td>
                    </tr>
                `;
            } else {
                profileBowlingBody.innerHTML += `
                    <tr>
                        <td><strong>${fmt}</strong></td>
                        <td colspan="7" class="text-muted text-center">No bowling registered</td>
                    </tr>
                `;
            }
        });
    }

    function renderPlayerHistoryChart(runsHistory) {
        if (runsChart) {
            runsChart.destroy();
        }

        const ctx = document.getElementById("runsHistoryChart").getContext("2d");
        
        if (!runsHistory || runsHistory.length === 0) {
            ctx.clearRect(0, 0, 400, 400);
            return;
        }

        const labels = runsHistory.map(item => item.date);
        const runs = runsHistory.map(item => item.runs);
        const colors = runsHistory.map(item => item.format === 'Test' ? '#3b82f6' : '#f59e0b');

        runsChart = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [{
                    label: 'Runs Scored',
                    data: runs,
                    backgroundColor: colors,
                    borderColor: 'rgba(255, 255, 255, 0.07)',
                    borderWidth: 1,
                    borderRadius: 4
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        display: false
                    },
                    tooltip: {
                        callbacks: {
                            label: function(context) {
                                const index = context.dataIndex;
                                const matchFormat = runsHistory[index].format;
                                return `Runs: ${context.parsed.y} (${matchFormat})`;
                            }
                        }
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        grid: {
                            color: 'rgba(255, 255, 255, 0.05)'
                        },
                        ticks: {
                            color: '#9ca3af'
                        }
                    },
                    x: {
                        grid: {
                            display: false
                        },
                        ticks: {
                            color: '#9ca3af',
                            maxRotation: 45,
                            minRotation: 45
                        }
                    }
                }
            }
        });
    }

    // --- SQL Playground Console ---
    function setupSQLPlayground() {
        btnRunSql.addEventListener("click", async () => {
            const sql = sqlInput.value.trim();
            if (!sql) return;

            sqlTableContainer.innerHTML = `
                <div class="spinner-container">
                    <div class="spinner"></div>
                    <p>Executing SQL query on dataset...</p>
                </div>
            `;
            sqlRowCount.textContent = "";

            try {
                const res = await fetch("/api/query", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json"
                    },
                    body: JSON.stringify({ sql })
                });

                const data = await res.json();
                
                if (!res.ok) {
                    throw new Error(data.detail || "SQL statement failed");
                }

                sqlRowCount.textContent = `${data.row_count} rows`;
                renderSQLTable(data);
            } catch (err) {
                console.error("SQL query error:", err);
                sqlRowCount.textContent = "Error";
                sqlTableContainer.innerHTML = `
                    <div class="empty-state-small" style="color: #ef4444;">
                        <i class="fa-solid fa-triangle-exclamation"></i>
                        <p>${err.message}</p>
                    </div>
                `;
            }
        });

        // SQL Preset click listeners
        presetBtns.forEach(btn => {
            btn.addEventListener("click", () => {
                const presetSql = btn.getAttribute("data-sql");
                sqlInput.value = presetSql;
                btnRunSql.click();
            });
        });
    }

    function renderSQLTable(data) {
        if (data.row_count === 0) {
            sqlTableContainer.innerHTML = `
                <div class="empty-state-small">
                    <i class="fa-solid fa-magnifying-glass"></i>
                    <p>Query completed successfully but returned 0 rows.</p>
                </div>
            `;
            return;
        }

        let headerHtml = "<tr>";
        data.columns.forEach(col => {
            headerHtml += `<th>${col}</th>`;
        });
        headerHtml += "</tr>";

        let rowsHtml = "";
        data.rows.forEach(row => {
            rowsHtml += "<tr>";
            data.columns.forEach(col => {
                const val = row[col];
                rowsHtml += `<td>${val !== null ? val : '<span class="text-dark">null</span>'}</td>`;
            });
            rowsHtml += "</tr>";
        });

        sqlTableContainer.innerHTML = `
            <table class="stats-table">
                <thead>
                    ${headerHtml}
                </thead>
                <tbody>
                    ${rowsHtml}
                </tbody>
            </table>
        `;
    }

    // --- Helpers ---
    function debounce(func, wait) {
        let timeout;
        return function(...args) {
            const context = this;
            clearTimeout(timeout);
            timeout = setTimeout(() => func.apply(context, args), wait);
        };
    }

    // ==========================================================================
    // TRUTH-O-METER — Claim Verification Engine
    // ==========================================================================

    function setupTruthOMeter() {
        // DOM references
        const modeTextBtn   = document.getElementById("mode-text-btn");
        const modeFileBtn   = document.getElementById("mode-file-btn");
        const textPanel     = document.getElementById("verify-text-panel");
        const filePanel     = document.getElementById("verify-file-panel");

        const claimTextarea = document.getElementById("claim-textarea");
        const charCount     = document.getElementById("claim-char-count");
        const btnVerify     = document.getElementById("btn-verify-claim");
        const verdictContainer = document.getElementById("single-verdict-container");

        const exampleBtns   = document.querySelectorAll(".example-btn");

        const dropZone      = document.getElementById("file-drop-zone");
        const fileInput     = document.getElementById("file-input");
        const btnBrowse     = document.getElementById("btn-browse-file");
        const fileSelectedInfo = document.getElementById("file-selected-info");
        const fileDropZoneEl   = document.getElementById("file-drop-zone");
        const selectedFileName = document.getElementById("selected-file-name");
        const selectedFileSize = document.getElementById("selected-file-size");
        const btnRemoveFile    = document.getElementById("btn-remove-file");
        const btnAnalyzeDoc    = document.getElementById("btn-analyze-doc");
        const maxClaimsSelect  = document.getElementById("max-claims-select");
        const docVerdictsCont  = document.getElementById("doc-verdicts-container");

        let selectedFile = null;

        // ── Mode Toggle ───────────────────────────────────────────────────────
        modeTextBtn.addEventListener("click", () => {
            modeTextBtn.classList.add("active");
            modeFileBtn.classList.remove("active");
            textPanel.classList.remove("hidden");
            filePanel.classList.add("hidden");
        });

        modeFileBtn.addEventListener("click", () => {
            modeFileBtn.classList.add("active");
            modeTextBtn.classList.remove("active");
            filePanel.classList.remove("hidden");
            textPanel.classList.add("hidden");
        });

        // ── Char Counter ──────────────────────────────────────────────────────
        claimTextarea.addEventListener("input", () => {
            charCount.textContent = `${claimTextarea.value.length} / 2000`;
        });

        // ── Example Claims ────────────────────────────────────────────────────
        exampleBtns.forEach(btn => {
            btn.addEventListener("click", () => {
                claimTextarea.value = btn.getAttribute("data-claim");
                charCount.textContent = `${claimTextarea.value.length} / 2000`;
            });
        });

        // ── Single Claim Verify ───────────────────────────────────────────────
        btnVerify.addEventListener("click", async () => {
            const claim = claimTextarea.value.trim();
            if (!claim) { claimTextarea.focus(); return; }

            btnVerify.disabled = true;
            verdictContainer.classList.remove("hidden");
            verdictContainer.innerHTML = buildLoadingHTML("Analyzing claim through 4-phase pipeline…");

            try {
                const res = await fetch("/api/v1/verify/claim", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ claim, skip_predictions: false })
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || "Verification failed");

                verdictContainer.innerHTML = "";
                if (data.is_multi_claim && Array.isArray(data.verdicts)) {
                    data.verdicts.forEach(v => {
                        verdictContainer.appendChild(buildVerdictCard(v));
                    });
                } else {
                    verdictContainer.appendChild(buildVerdictCard(data));
                }
            } catch (err) {
                verdictContainer.innerHTML = `
                    <div class="verdict-card">
                        <div class="verdict-header error">
                            <span class="verdict-pill error"><i class="fa-solid fa-circle-xmark"></i> Error</span>
                        </div>
                        <div class="verdict-error-msg"><i class="fa-solid fa-triangle-exclamation" style="color:#ef4444;margin-right:6px;"></i>${err.message}</div>
                    </div>`;
            } finally {
                btnVerify.disabled = false;
            }
        });

        // ── File Upload — Browse & Drag-Drop ──────────────────────────────────
        btnBrowse.addEventListener("click", () => fileInput.click());
        dropZone.addEventListener("click", (e) => {
            if (e.target !== btnBrowse && !btnBrowse.contains(e.target)) {
                fileInput.click();
            }
        });

        fileInput.addEventListener("change", () => {
            if (fileInput.files && fileInput.files[0]) {
                handleFileSelected(fileInput.files[0]);
            }
        });

        dropZone.addEventListener("dragover", (e) => {
            e.preventDefault();
            dropZone.classList.add("drag-over");
        });
        dropZone.addEventListener("dragleave", () => dropZone.classList.remove("drag-over"));
        dropZone.addEventListener("drop", (e) => {
            e.preventDefault();
            dropZone.classList.remove("drag-over");
            const f = e.dataTransfer.files[0];
            if (f) handleFileSelected(f);
        });

        function handleFileSelected(file) {
            const ext = file.name.split(".").pop().toLowerCase();
            if (!["txt", "pdf"].includes(ext)) {
                alert("Only .txt and .pdf files are supported.");
                return;
            }
            selectedFile = file;
            selectedFileName.textContent = file.name;
            selectedFileSize.textContent = formatBytes(file.size);
            fileDropZoneEl.classList.add("hidden");
            fileSelectedInfo.classList.remove("hidden");
            docVerdictsCont.classList.add("hidden");
            docVerdictsCont.innerHTML = "";
        }

        btnRemoveFile.addEventListener("click", () => {
            selectedFile = null;
            fileInput.value = "";
            fileDropZoneEl.classList.remove("hidden");
            fileSelectedInfo.classList.add("hidden");
            docVerdictsCont.classList.add("hidden");
        });

        // ── Analyze Document ──────────────────────────────────────────────────
        btnAnalyzeDoc.addEventListener("click", async () => {
            if (!selectedFile) return;

            const maxClaims = maxClaimsSelect.value;
            btnAnalyzeDoc.disabled = true;

            docVerdictsCont.classList.remove("hidden");
            docVerdictsCont.innerHTML = buildLoadingHTML(
                `Extracting claims from "${selectedFile.name}"…`,
                "This may take 30–120 seconds depending on document size and number of claims."
            );

            const formData = new FormData();
            formData.append("file", selectedFile);

            try {
                const res = await fetch(`/api/v1/verify/file?max_claims=${maxClaims}&skip_predictions=true`, {
                    method: "POST",
                    body: formData
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || "IDP pipeline failed");

                docVerdictsCont.innerHTML = "";
                docVerdictsCont.appendChild(buildDocVerdictsList(data));
            } catch (err) {
                docVerdictsCont.innerHTML = `
                    <div style="color:#ef4444;padding:1rem;background:rgba(239,68,68,0.08);border-radius:10px;border:1px solid rgba(239,68,68,0.2);">
                        <i class="fa-solid fa-triangle-exclamation"></i> ${err.message}
                    </div>`;
            } finally {
                btnAnalyzeDoc.disabled = false;
            }
        });
    }

    // ==========================================================================
    // Verdict Rendering Utilities
    // ==========================================================================

    function getVerdictClass(verdict, status) {
        if (!verdict || status === "error" || status === "no_data") return "error";
        const v = verdict.toLowerCase();
        if (v === "true" || v === "confirmed" || v === "verified_fact") return "true";
        if (v === "false" || v === "refuted") return "false";
        if (v === "approximate" || v === "close" || v === "plausible" || v === "minor_deviation" || v === "inaccurate") return "approx";
        if (v === "informational") return "info";
        return "error";
    }

    function getVerdictIcon(cls) {
        const icons = {
            true:  "fa-circle-check",
            false: "fa-circle-xmark",
            approx:"fa-circle-exclamation",
            info:  "fa-circle-info",
            error: "fa-triangle-exclamation",
        };
        return icons[cls] || "fa-circle-question";
    }

    function getVerdictLabel(verdict, status) {
        if (!verdict) return status === "no_data" ? "No Data" : "Error";
        const v = verdict.toLowerCase();
        if (v === "true" || v === "confirmed") return "TRUE";
        if (v === "false" || v === "refuted") return "FALSE";
        if (v === "approximate") return "APPROXIMATE";
        if (v === "informational") return "INFO";
        return verdict.replace(/_/g, ' ').toUpperCase();
    }

    function buildLoadingHTML(msg = "Processing…", subMsg = "") {
        return `
            <div class="verify-loading">
                <div class="spinner"></div>
                <p>${msg}</p>
                ${subMsg ? `<div class="phase-log">${subMsg}</div>` : ""}
            </div>`;
    }

    function buildVerdictCard(data) {
        const status  = data.status || "error";
        const verdict = data.verdict;
        const cls     = getVerdictClass(verdict, status);
        const label   = getVerdictLabel(verdict, status);
        const icon    = getVerdictIcon(cls);
        const accuracy = data.accuracy_pct != null ? data.accuracy_pct.toFixed(1) : null;

        const card = document.createElement("div");
        card.className = "verdict-card";

        // 1. Split Layout (Left: Circular Gauge, Right: Stats Grid)
        const splitLayout = document.createElement("div");
        splitLayout.className = "verdict-split-layout";

        // Left Side: Circular SVG Gauge
        const gaugeWrapper = document.createElement("div");
        gaugeWrapper.className = "verdict-gauge-wrapper";
        
        let color = "#6b7280"; // Default grey
        if (cls === "true") color = "#10b981";
        else if (cls === "false") color = "#ef4444";
        else if (cls === "approx") color = "#f59e0b";
        else if (cls === "info") color = "#3b82f6";

        const percentVal = accuracy !== null ? parseFloat(accuracy) : 0;
        // Stroke dasharray circumference is 2 * PI * r = 2 * 3.14159 * 40 = 251.3
        const circ = 251.3;
        const offset = circ - (percentVal / 100) * circ;

        gaugeWrapper.innerHTML = `
            <svg class="gauge-svg" viewBox="0 0 100 100">
                <circle class="gauge-bg" cx="50" cy="50" r="40" stroke="rgba(255,255,255,0.03)" stroke-width="8" fill="none" />
                <circle class="gauge-fill ${cls}" cx="50" cy="50" r="40" stroke="${color}" stroke-dasharray="${circ}" stroke-dashoffset="${offset}" stroke-linecap="round" stroke-width="8" fill="none" transform="rotate(-90 50 50)" style="filter: drop-shadow(0 0 6px ${color}80)" />
            </svg>
            <div class="gauge-content">
                <span class="percentage">${accuracy !== null ? Math.round(percentVal) + "%" : "N/A"}</span>
                <span class="verdict-label ${cls}">${label}</span>
            </div>
        `;
        splitLayout.appendChild(gaugeWrapper);

        // Right Side: Stats Grid
        const statsGrid = document.createElement("div");
        statsGrid.className = "verdict-stats-grid";

        if (status === "ok") {
            const stats = [
                { label: "Player", value: data.subject || "—" },
                { label: "Metric", value: data.metric || "—" },
                { label: "Claimed", value: data.claimed_value != null ? data.claimed_value : "—" },
                { label: "Actual", value: data.real_val != null ? parseFloat(data.real_val).toFixed(4) : "—" },
                { label: "Sample Size", value: data.sample_size != null ? data.sample_size.toLocaleString() + " balls" : "—" },
                { label: "Confidence", value: data.confidence != null ? (parseFloat(data.confidence) * 100).toFixed(0) + "%" : "—" },
            ];

            const filters = data.filters || {};
            const activeFilters = Object.entries(filters)
                .filter(([k, v]) => v !== null && v !== undefined && !k.startsWith("_") && k !== "location" && k !== "as_of_date")
                .map(([k, v]) => {
                    const cleanK = k.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
                    return `${cleanK}: ${v}`;
                });
            if (activeFilters.length) {
                stats.push({ label: "Applied Filters", value: activeFilters.join(" | ") });
            }

            stats.forEach(s => {
                const div = document.createElement("div");
                div.className = "verdict-stat";
                div.innerHTML = `
                    <span class="verdict-stat-label">${s.label}</span>
                    <span class="verdict-stat-value">${s.value}</span>
                `;
                statsGrid.appendChild(div);
            });
            splitLayout.appendChild(statsGrid);
        } else {
            // Error layout in stats grid
            const errorDiv = document.createElement("div");
            errorDiv.className = "verdict-stat";
            errorDiv.innerHTML = `
                <span class="verdict-stat-label">Calculation Status</span>
                <span class="verdict-stat-value" style="color: #ef4444;">${escapeHtml(data.message || "Failed to process claim")}</span>
            `;
            statsGrid.appendChild(errorDiv);
            splitLayout.appendChild(statsGrid);
        }

        card.appendChild(splitLayout);

        // 2. Original claim text block
        if (data.claim) {
            const claimDiv = document.createElement("div");
            claimDiv.className = "verdict-claim-text";
            claimDiv.innerHTML = `<i class="fa-solid fa-quote-left" style="margin-right:6px;color:#a78bfa;"></i>${escapeHtml(data.claim)}`;
            card.appendChild(claimDiv);
        }

        // 3. AI/LSTM Predictions Panel
        const preds = data.predictions;
        if (preds && !preds.error) {
            const predPanel = document.createElement("div");
            predPanel.className = "ai-forecast-panel";

            // Header
            const predHeader = document.createElement("div");
            predHeader.className = "ai-forecast-header";
            predHeader.innerHTML = `
                <span class="ai-badge"><i class="fa-solid fa-brain"></i> AI Forecast</span>
                <span class="ai-forecast-subtitle">${preds.model || 'GBM Ensemble'} · Next Match Prediction</span>
                ${preds.confidence != null ? `<span class="ai-confidence-badge">${preds.confidence}% confidence</span>` : ""}
            `;
            predPanel.appendChild(predHeader);

            const predGrid = document.createElement("div");
            predGrid.className = "ai-forecast-grid";

            const isBatting = preds.expected_runs != null || preds.prob_50 != null;
            const isBowling = preds.expected_wickets != null;

            if (isBatting) {
                const forecastItems = [
                    { icon: "fa-chart-line", label: "Expected Runs", value: preds.expected_average_range || (preds.expected_runs != null ? `~${preds.expected_runs}` : "—"), highlight: true },
                    { icon: "fa-bolt", label: "Expected Strike Rate", value: preds.expected_sr != null ? preds.expected_sr : "—" },
                    { icon: "fa-star-half-stroke", label: "Prob. of 50+", value: preds.prob_50 != null ? `${preds.prob_50}%` : "—" },
                    { icon: "fa-star", label: "Prob. of 100+", value: preds.prob_100 != null ? `${preds.prob_100}%` : "—" },
                ];
                forecastItems.forEach(item => {
                    const el = document.createElement("div");
                    el.className = `ai-forecast-item${item.highlight ? " highlight" : ""}`;
                    el.innerHTML = `
                        <i class="fa-solid ${item.icon}"></i>
                        <div class="ai-forecast-item-body">
                            <span class="ai-forecast-item-label">${item.label}</span>
                            <span class="ai-forecast-item-value">${item.value}</span>
                        </div>
                    `;
                    predGrid.appendChild(el);
                });
            } else if (isBowling) {
                const forecastItems = [
                    { icon: "fa-fire", label: "Expected Wickets", value: preds.expected_wickets != null ? preds.expected_wickets : "—", highlight: true },
                    { icon: "fa-gauge-high", label: "Expected Economy", value: preds.expected_economy != null ? preds.expected_economy : "—" },
                ];
                forecastItems.forEach(item => {
                    const el = document.createElement("div");
                    el.className = `ai-forecast-item${item.highlight ? " highlight" : ""}`;
                    el.innerHTML = `
                        <i class="fa-solid ${item.icon}"></i>
                        <div class="ai-forecast-item-body">
                            <span class="ai-forecast-item-label">${item.label}</span>
                            <span class="ai-forecast-item-value">${item.value}</span>
                        </div>
                    `;
                    predGrid.appendChild(el);
                });
            }

            predPanel.appendChild(predGrid);
            card.appendChild(predPanel);
        }

        // 4. AI Intelligence Insight
        if (data.insight) {
            const insightDiv = document.createElement("div");
            insightDiv.className = "verdict-insight";
            insightDiv.innerHTML = `<i class="fa-solid fa-lightbulb" style="color: #f59e0b; margin-top: 3px;"></i><span>${escapeHtml(data.insight)}</span>`;
            card.appendChild(insightDiv);
        }

        return card;
    }

    function buildDocVerdictsList(data) {
        const container = document.createElement("div");

        // Summary banner
        const verdicts = data.verdicts || [];
        const counts   = { true: 0, false: 0, approx: 0, other: 0 };
        verdicts.forEach(v => {
            const cls = getVerdictClass(v.verdict, v.status);
            if (cls === "true")  counts.true++;
            else if (cls === "false")  counts.false++;
            else if (cls === "approx") counts.approx++;
            else counts.other++;
        });

        container.innerHTML = `
            <div class="doc-summary-banner">
                <h4><i class="fa-solid fa-file-lines" style="margin-right:8px;color:#a78bfa;"></i>
                    ${escapeHtml(data.filename)} — ${data.claims_found} claim${data.claims_found !== 1 ? 's' : ''} found (${data.file_size_kb} KB)
                </h4>
                <div class="doc-summary-pills">
                    ${counts.true  ? `<span class="count-pill true"><i class="fa-solid fa-circle-check"></i> ${counts.true} True</span>` : ""}
                    ${counts.false ? `<span class="count-pill false"><i class="fa-solid fa-circle-xmark"></i> ${counts.false} False</span>` : ""}
                    ${counts.approx? `<span class="count-pill approx"><i class="fa-solid fa-circle-exclamation"></i> ${counts.approx} Approx</span>` : ""}
                    ${counts.other ? `<span class="count-pill other"><i class="fa-solid fa-circle-question"></i> ${counts.other} Other</span>` : ""}
                </div>
            </div>
        `;

        const list = document.createElement("div");
        list.className = "doc-verdicts-list";

        if (verdicts.length === 0) {
            list.innerHTML = `
                <div class="empty-state">
                    <i class="fa-solid fa-file-circle-question"></i>
                    <h3>No Claims Detected</h3>
                    <p>The document did not contain verifiable cricket statistical assertions.</p>
                </div>`;
        } else {
            verdicts.forEach((v, i) => {
                const cls   = getVerdictClass(v.verdict, v.status);
                const label = getVerdictLabel(v.verdict, v.status);
                const icon  = getVerdictIcon(cls);
                const acc   = v.accuracy_pct != null ? v.accuracy_pct.toFixed(1) + "%" : "—";

                const item = document.createElement("div");
                item.className = "doc-verdict-item";

                const header = document.createElement("div");
                header.className = `doc-verdict-header ${cls}`;
                header.innerHTML = `
                    <div class="doc-verdict-title">
                        <span class="claim-num">${i + 1}</span>
                        <span class="verdict-pill ${cls}" style="font-size:0.72rem;padding:0.2rem 0.6rem;">
                            <i class="fa-solid ${icon}"></i> ${label}
                        </span>
                        <span class="doc-verdict-claim" title="${escapeHtml(v.claim || '')}">${escapeHtml((v.claim || 'No claim text').substring(0, 100))}${(v.claim || '').length > 100 ? '…' : ''}</span>
                    </div>
                    <div class="doc-verdict-meta">
                        ${v.subject ? `<span><i class="fa-solid fa-user" style="margin-right:3px;"></i>${escapeHtml(v.subject)}</span>` : ""}
                        <span>${acc}</span>
                        <span>${v.elapsed_ms ? v.elapsed_ms + "ms" : ""}</span>
                        <i class="fa-solid fa-chevron-down chevron-icon"></i>
                    </div>
                `;

                const body = document.createElement("div");
                body.className = "doc-verdict-body";

                const bodyStats = [
                    { label: "Player", value: v.subject || "—" },
                    { label: "Metric", value: v.metric || "—" },
                    { label: "Claimed", value: v.claimed_value != null ? v.claimed_value : "—" },
                    { label: "Actual", value: v.real_val != null ? parseFloat(v.real_val).toFixed(4) : "—" },
                    { label: "Sample", value: v.sample_size ? v.sample_size.toLocaleString() + " balls" : "—" },
                    { label: "Accuracy", value: acc },
                ];
                if (v.message && v.status !== "ok") {
                    bodyStats.push({ label: "Note", value: v.message });
                }
                bodyStats.forEach(s => {
                    const div = document.createElement("div");
                    div.className = "verdict-stat";
                    div.innerHTML = `
                        <span class="verdict-stat-label">${s.label}</span>
                        <span class="verdict-stat-value" style="font-size:0.88rem;">${escapeHtml(String(s.value))}</span>
                    `;
                    body.appendChild(div);
                });

                // Toggle expand/collapse
                let expanded = false;
                header.addEventListener("click", () => {
                    expanded = !expanded;
                    body.classList.toggle("open", expanded);
                    header.querySelector(".chevron-icon").classList.toggle("open", expanded);
                });

                item.appendChild(header);
                item.appendChild(body);
                list.appendChild(item);
            });
        }

        container.appendChild(list);
        return container;
    }

    // ── Utility Helpers ────────────────────────────────────────────────────────
    function escapeHtml(str) {
        if (str === null || str === undefined) return "";
        return String(str)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;");
    }

    function formatBytes(bytes) {
        if (bytes < 1024) return `${bytes} B`;
        if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
        return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    }

    // Initialize Truth-O-Meter
    setupTruthOMeter();
});

