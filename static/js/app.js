// Fashion Check - Main JavaScript Application

class FashionCheckApp {
	constructor() {
		this.apiBase = "/api";
		this.websocket = null;
		this.cameraStream = null;
		this.cameraInterval = null;
		this.currentUser = null;
		// Don't auto-initialize to avoid auth loop
	}

	async init() {
		// Only initialize if we're on the main app page (not login/register)
		if (
			window.location.pathname === "/" ||
			window.location.pathname.includes("index")
		) {
			await this.checkAuthenticationStatus();
		}
		this.setupEventListeners();
		this.setupDragAndDrop();
		this.loadAnalysisTypes();
	}

	async checkAuthenticationStatus() {
		try {
			const response = await fetch("/auth/check");
			const data = await response.json();

			if (data.authenticated) {
				this.currentUser = data.user;
				this.updateUserInterface();
			}
			// Don't redirect if not authenticated - let backend handle it
		} catch (error) {
			console.error("Authentication check failed:", error);
			// Don't redirect on error - let backend handle it
		}
	}

	updateUserInterface() {
		if (this.currentUser) {
			document.getElementById("username").textContent =
				this.currentUser.username;
		}
	}

	setupEventListeners() {
		// File input change
		document
			.getElementById("image-input")
			.addEventListener("change", (e) => {
				this.handleFileSelect(e.target.files[0]);
			});

		// Analyze button
		document.getElementById("analyzeBtn").addEventListener("click", () => {
			this.analyzeImage();
		});

		// Test button
		document.getElementById("testBtn").addEventListener("click", () => {
			this.testAnalysis();
		});

		// Clear button
		document.getElementById("clearBtn").addEventListener("click", () => {
			this.clearImage();
		});

		// Camera controls
		document
			.getElementById("startCameraBtn")
			.addEventListener("click", () => {
				this.startCamera();
			});

		document
			.getElementById("stopCameraBtn")
			.addEventListener("click", () => {
				this.stopCamera();
			});

		// Upload area click
		document.getElementById("upload-area").addEventListener("click", () => {
			document.getElementById("image-input").click();
		});

		// Logout functionality
		document.getElementById("logoutLink").addEventListener("click", (e) => {
			e.preventDefault();
			this.logout();
		});

		// Smooth scrolling for navigation links
		document.querySelectorAll('a[href^="#"]').forEach((anchor) => {
			anchor.addEventListener("click", function (e) {
				e.preventDefault();
				const target = document.querySelector(
					this.getAttribute("href")
				);
				if (target) {
					target.scrollIntoView({
						behavior: "smooth",
						block: "start",
					});
				}
			});
		});
	}

	setupDragAndDrop() {
		const uploadArea = document.getElementById("upload-area");

		["dragenter", "dragover", "dragleave", "drop"].forEach((eventName) => {
			uploadArea.addEventListener(eventName, this.preventDefaults, false);
		});

		["dragenter", "dragover"].forEach((eventName) => {
			uploadArea.addEventListener(
				eventName,
				() => {
					uploadArea.classList.add("dragover");
				},
				false
			);
		});

		["dragleave", "drop"].forEach((eventName) => {
			uploadArea.addEventListener(
				eventName,
				() => {
					uploadArea.classList.remove("dragover");
				},
				false
			);
		});

		uploadArea.addEventListener(
			"drop",
			(e) => {
				const files = e.dataTransfer.files;
				if (files.length > 0) {
					this.handleFileSelect(files[0]);
				}
			},
			false
		);
	}

	preventDefaults(e) {
		e.preventDefault();
		e.stopPropagation();
	}

	async loadAnalysisTypes() {
		try {
			const response = await fetch(`${this.apiBase}/analysis-types`);
			const data = await response.json();

			const select = document.getElementById("analysisType");
			select.innerHTML = "";

			data.analysis_types.forEach((type) => {
				const option = document.createElement("option");
				option.value = type.id;
				option.textContent = type.name;
				option.title = type.description;
				select.appendChild(option);
			});
		} catch (error) {
			console.error("Failed to load analysis types:", error);
		}
	}

	handleFileSelect(file) {
		if (!file) return;

		// Validate file type
		const allowedTypes = [
			"image/jpeg",
			"image/jpg",
			"image/png",
			"image/gif",
			"image/bmp",
			"image/webp",
		];
		if (!allowedTypes.includes(file.type)) {
			this.showAlert(
				"Please select a valid image file (JPEG, PNG, GIF, BMP, WebP)",
				"danger"
			);
			return;
		}

		// Validate file size (10MB)
		if (file.size > 10 * 1024 * 1024) {
			this.showAlert("File size must be less than 10MB", "danger");
			return;
		}

		// Show preview
		const reader = new FileReader();
		reader.onload = (e) => {
			const previewImg = document.getElementById("previewImg");
			previewImg.src = e.target.result;

			document.getElementById("imagePreview").style.display = "block";
			document.getElementById("upload-area").style.display = "none";
		};
		reader.readAsDataURL(file);

		// Store file for analysis
		this.selectedFile = file;
	}

	async analyzeImage() {
		console.log("analyzeImage method called");
		console.log("selectedFile:", this.selectedFile);

		if (!this.selectedFile) {
			console.log("No file selected, returning early");
			alert("Please select an image first");
			return;
		}

		const analysisType = document.getElementById("analysis-type").value;
		const loadingSpinner = document.getElementById("loading-spinner");
		const analyzeBtn = document.getElementById("analyze-btn");

		console.log("analysisType:", analysisType);
		console.log("loadingSpinner:", loadingSpinner);
		console.log("analyzeBtn:", analyzeBtn);

		try {
			// Show loading
			loadingSpinner.style.display = "block";
			analyzeBtn.disabled = true;

			// Create form data
			const formData = new FormData();
			formData.append("file", this.selectedFile);

			// Make API request
			const response = await fetch(
				`${this.apiBase}/analyze-image?analysis_type=${analysisType}`,
				{
					method: "POST",
					body: formData,
				}
			);

			console.log("Response status:", response.status);
			console.log("Response headers:", response.headers);

			if (!response.ok) {
				throw new Error(`HTTP error! status: ${response.status}`);
			}

			const result = await response.json();
			console.log("Analysis result:", result);

			if (result.success) {
				this.displayAnalysisResults(result.analysis);
				this.showAlert("Analysis completed successfully!", "success");
			} else {
				this.showAlert(`Analysis failed: ${result.error}`, "danger");
			}
		} catch (error) {
			console.error("Analysis error:", error);
			this.showAlert(
				"Failed to analyze image. Please try again.",
				"danger"
			);
		} finally {
			// Hide loading
			loadingSpinner.style.display = "none";
			analyzeBtn.disabled = false;
		}
	}

	async testAnalysis() {
		try {
			const response = await fetch(`${this.apiBase}/test-analysis`, {
				method: "POST",
			});

			console.log("Test response status:", response.status);

			if (!response.ok) {
				throw new Error(`HTTP error! status: ${response.status}`);
			}

			const result = await response.json();
			console.log("Test result:", result);

			if (result.success) {
				this.displayAnalysisResults(result.analysis);
				this.showAlert("Test analysis completed!", "success");
			} else {
				this.showAlert(`Test failed: ${result.error}`, "danger");
			}
		} catch (error) {
			console.error("Test error:", error);
			this.showAlert("Test failed. Check console for details.", "danger");
		}
	}

	displayAnalysisResults(analysis) {
		const resultsContainer = document.getElementById("analysisResults");
		resultsContainer.innerHTML = "";

		console.log("Displaying analysis:", analysis);

		// Check for raw_analysis (markdown format)
		if (analysis.raw_analysis) {
			resultsContainer.innerHTML = `
				<div class="analysis-result">
					<h5><i class="fas fa-magic me-2"></i>Fashion Analysis</h5>
					<div class="analysis-text">${this.formatMarkdownText(
						analysis.raw_analysis
					)}</div>
				</div>
			`;
		} else if (typeof analysis === "string") {
			// Handle raw text directly
			resultsContainer.innerHTML = `
				<div class="analysis-result">
					<h5><i class="fas fa-magic me-2"></i>Fashion Analysis</h5>
					<div class="analysis-text">${this.formatMarkdownText(analysis)}</div>
				</div>
			`;
		} else if (
			analysis.structured &&
			typeof analysis.structured === "object"
		) {
			// Fallback to structured analysis if available
			this.renderStructuredAnalysis(
				analysis.structured,
				resultsContainer
			);
		}

		// Scroll to results
		resultsContainer.scrollIntoView({
			behavior: "smooth",
			block: "nearest",
		});
	}

	renderStructuredAnalysis(analysis, container) {
		// Overall Rating
		if (analysis.overall_rating || analysis.rating) {
			const rating = analysis.overall_rating || analysis.rating;
			container.appendChild(this.createRatingSection(rating));
		}

		// What Works Well
		if (analysis.strengths || analysis.what_works_well) {
			const strengths = analysis.strengths || analysis.what_works_well;
			container.appendChild(
				this.createSection(
					"What Works Well",
					strengths,
					"success",
					"thumbs-up"
				)
			);
		}

		// Areas for Improvement
		if (
			analysis.improvements ||
			analysis.areas_for_improvement ||
			analysis.what_could_be_improved
		) {
			const improvements =
				analysis.improvements ||
				analysis.areas_for_improvement ||
				analysis.what_could_be_improved;
			container.appendChild(
				this.createSection(
					"Areas for Improvement",
					improvements,
					"warning",
					"exclamation-triangle"
				)
			);
		}

		// Color Analysis
		if (analysis.color_analysis) {
			container.appendChild(
				this.createSection(
					"Color Analysis",
					analysis.color_analysis,
					"info",
					"palette"
				)
			);
		}

		// Style Recommendations
		if (analysis.recommendations || analysis.style_recommendations) {
			const recommendations =
				analysis.recommendations || analysis.style_recommendations;
			container.appendChild(
				this.createSection(
					"Style Recommendations",
					recommendations,
					"primary",
					"lightbulb"
				)
			);
		}

		// Fit & Silhouette
		if (analysis.fit_analysis || analysis.silhouette) {
			const fitData = analysis.fit_analysis || analysis.silhouette;
			container.appendChild(
				this.createSection(
					"Fit & Silhouette",
					fitData,
					"secondary",
					"user-tie"
				)
			);
		}

		// Occasion Appropriateness
		if (analysis.occasion_appropriateness) {
			container.appendChild(
				this.createSection(
					"Occasion Suitability",
					analysis.occasion_appropriateness,
					"info",
					"calendar-alt"
				)
			);
		}

		// Professional Analysis
		if (analysis.professional_rating) {
			container.appendChild(
				this.createSection(
					"Professional Assessment",
					analysis.professional_rating,
					"dark",
					"briefcase"
				)
			);
		}

		// Key Styling Tip
		if (analysis.key_tip || analysis.styling_tip) {
			const tip = analysis.key_tip || analysis.styling_tip;
			container.appendChild(this.createTipSection(tip));
		}
	}

	createRatingSection(rating) {
		const section = document.createElement("div");
		section.className = "analysis-result";

		let ratingValue =
			typeof rating === "object" ? rating.score || rating.value : rating;
		if (typeof ratingValue === "string") {
			ratingValue = parseFloat(
				ratingValue.match(/\d+(\.\d+)?/)?.[0] || "0"
			);
		}

		const stars = this.generateStars(ratingValue);
		const statusClass = this.getRatingStatus(ratingValue);

		section.innerHTML = `
			<h5><i class="fas fa-star me-2"></i>Overall Rating</h5>
			<div class="rating-display">
				<span class="rating-stars">${stars}</span>
				<span class="rating-number">${ratingValue}/10</span>
				<span class="status-indicator ${statusClass}"></span>
			</div>
			${
				typeof rating === "object" && rating.description
					? `<p>${rating.description}</p>`
					: ""
			}
		`;

		return section;
	}

	createSection(title, content, type = "primary", icon = "info-circle") {
		const section = document.createElement("div");
		section.className = "analysis-result";

		let contentHtml = "";
		if (Array.isArray(content)) {
			contentHtml = content.map((item) => `<li>${item}</li>`).join("");
			contentHtml = `<ul>${contentHtml}</ul>`;
		} else if (typeof content === "object") {
			contentHtml = Object.entries(content)
				.map(
					([key, value]) =>
						`<strong>${this.formatKey(key)}:</strong> ${value}`
				)
				.join("<br>");
		} else {
			contentHtml = content;
		}

		section.innerHTML = `
			<h5><i class="fas fa-${icon} me-2 text-${type}"></i>${title}</h5>
			<div>${contentHtml}</div>
		`;

		return section;
	}

	createTipSection(tip) {
		const section = document.createElement("div");
		section.className = "analysis-result";
		section.style.background =
			"linear-gradient(135deg, #667eea 0%, #764ba2 100%)";
		section.style.color = "white";

		section.innerHTML = `
			<h5><i class="fas fa-lightbulb me-2"></i>Key Styling Tip</h5>
			<p class="mb-0" style="font-size: 1.1rem;">${tip}</p>
		`;

		return section;
	}

	generateStars(rating) {
		const fullStars = Math.floor(rating / 2);
		const halfStar = rating % 2 >= 1;
		const emptyStars = 5 - fullStars - (halfStar ? 1 : 0);

		return (
			"★".repeat(fullStars) +
			(halfStar ? "☆" : "") +
			"☆".repeat(emptyStars)
		);
	}

	getRatingStatus(rating) {
		if (rating >= 8) return "status-excellent";
		if (rating >= 6) return "status-good";
		if (rating >= 4) return "status-fair";
		return "status-poor";
	}

	formatKey(key) {
		return key.replace(/_/g, " ").replace(/\b\w/g, (l) => l.toUpperCase());
	}

	formatMarkdownText(text) {
		let formatted = text
			// Headers
			.replace(
				/^### (.*$)/gim,
				'<h6 class="text-primary fw-bold mt-4 mb-2"><i class="fas fa-star me-2"></i>$1</h6>'
			)
			.replace(
				/^## (.*$)/gim,
				'<h5 class="text-primary fw-bold mt-4 mb-3"><i class="fas fa-magic me-2"></i>$1</h5>'
			)
			.replace(
				/^# (.*$)/gim,
				'<h4 class="text-primary fw-bold mt-4 mb-3">$1</h4>'
			)

			// Bold text
			.replace(/\*\*(.*?)\*\*/g, '<strong class="text-dark">$1</strong>')

			// Line breaks first
			.replace(/\n/g, "<br>")

			// Lists - handle indented lists better
			.replace(/^  - (.*$)/gim, '<li class="ms-3">$1</li>')
			.replace(/^- (.*$)/gim, "<li>$1</li>")
			.replace(/^(\d+)\. (.*$)/gim, "<li>$2</li>")

			// Emojis and icons
			.replace(
				/✅/g,
				'<i class="fas fa-check-circle text-success me-2"></i>'
			)
			.replace(/🔧/g, '<i class="fas fa-tools text-warning me-2"></i>')
			.replace(/💡/g, '<i class="fas fa-lightbulb text-info me-2"></i>')
			.replace(
				/👔/g,
				'<i class="fas fa-user-tie text-primary me-2"></i>'
			);

		// Wrap consecutive list items in ul tags
		formatted = formatted.replace(
			/(<li[^>]*>.*?<\/li>)(\s*<br>\s*<li[^>]*>.*?<\/li>)*/g,
			function (match) {
				return "<ul>" + match.replace(/<br>\s*/g, "") + "</ul>";
			}
		);

		// Clean up extra breaks
		formatted = formatted
			.replace(/<br><br>/g, "<br>")
			.replace(/<br>$/g, "")
			.replace(/<br><h/g, "<h")
			.replace(/<\/h([1-6])><br>/g, "</h$1>");

		return formatted;
	}

	formatAnalysisText(text) {
		// Handle JSON responses wrapped in markdown code blocks
		if (text.includes("```json") && text.includes("```")) {
			// Extract JSON from markdown code blocks
			const jsonMatch = text.match(/```json\s*([\s\S]*?)\s*```/);
			if (jsonMatch) {
				try {
					const jsonData = JSON.parse(jsonMatch[1]);
					return this.formatJsonAsHtml(jsonData);
				} catch (e) {
					console.log("Failed to parse JSON:", e);
					console.log("JSON text:", jsonMatch[1]);
				}
			}
		}

		// Convert plain text to HTML with proper formatting
		return text
			.replace(/\n/g, "<br>")
			.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
			.replace(/\*(.*?)\*/g, "<em>$1</em>")
			.replace(/```json/g, '<div class="code-block"><pre>')
			.replace(/```/g, "</pre></div>");
	}

	formatJsonAsHtml(jsonData) {
		let html = '<div class="analysis-sections">';

		for (const [sectionKey, sectionValue] of Object.entries(jsonData)) {
			html += `<div class="analysis-section mb-4 p-3 border rounded">`;
			html += `<h6 class="text-primary fw-bold mb-3">
				<i class="fas fa-star me-2"></i>${this.formatSectionTitle(sectionKey)}
			</h6>`;

			if (typeof sectionValue === "object" && sectionValue !== null) {
				html += `<div class="ms-3">`;
				for (const [key, value] of Object.entries(sectionValue)) {
					html += `<div class="mb-2">`;
					html += `<strong class="text-dark">${this.formatKey(
						key
					)}:</strong> `;

					if (typeof value === "number") {
						html += `<span class="badge bg-primary ms-2">${value}/10</span>`;
					} else {
						html += `<span class="text-muted">${value}</span>`;
					}
					html += `</div>`;
				}
				html += `</div>`;
			} else {
				html += `<p class="text-muted ms-3">${sectionValue}</p>`;
			}

			html += `</div>`;
		}

		html += "</div>";
		return html;
	}

	formatSectionTitle(title) {
		return title
			.replace(/([a-z])([A-Z])/g, "$1 $2")
			.replace(/^\w/, (c) => c.toUpperCase());
	}

	clearImage() {
		document.getElementById("imagePreview").style.display = "none";
		document.getElementById("upload-area").style.display = "block";
		document.getElementById("analysisResults").innerHTML = `
			<div class="text-center text-muted">
				<i class="fas fa-chart-bar fa-3x mb-3"></i>
				<p>Upload an image to see detailed fashion analysis here</p>
			</div>
		`;
		this.selectedFile = null;
		document.getElementById("image-input").value = "";
	}

	// Camera functionality
	async startCamera() {
		try {
			const constraints = {
				video: {
					width: { ideal: 1280 },
					height: { ideal: 720 },
					facingMode: "user",
				},
			};

			this.cameraStream = await navigator.mediaDevices.getUserMedia(
				constraints
			);
			const video = document.getElementById("cameraVideo");
			const placeholder = document.getElementById("cameraPlaceholder");

			video.srcObject = this.cameraStream;
			video.style.display = "block";
			placeholder.style.display = "none";

			document.getElementById("startCameraBtn").style.display = "none";
			document.getElementById("stopCameraBtn").style.display =
				"inline-block";

			// Connect WebSocket for live analysis
			this.connectWebSocket();

			// Start periodic analysis
			this.startLiveAnalysis();
		} catch (error) {
			console.error("Camera access error:", error);
			this.showAlert(
				"Failed to access camera. Please check permissions.",
				"danger"
			);
		}
	}

	stopCamera() {
		if (this.cameraStream) {
			this.cameraStream.getTracks().forEach((track) => track.stop());
			this.cameraStream = null;
		}

		if (this.websocket) {
			this.websocket.close();
			this.websocket = null;
		}

		if (this.cameraInterval) {
			clearInterval(this.cameraInterval);
			this.cameraInterval = null;
		}

		const video = document.getElementById("cameraVideo");
		const placeholder = document.getElementById("cameraPlaceholder");

		video.style.display = "none";
		placeholder.style.display = "block";

		document.getElementById("startCameraBtn").style.display =
			"inline-block";
		document.getElementById("stopCameraBtn").style.display = "none";

		// Clear live results
		document.getElementById("liveResults").innerHTML = `
			<div class="text-center text-muted">
				<i class="fas fa-clock fa-3x mb-3"></i>
				<p>Start camera to see real-time fashion analysis</p>
			</div>
		`;
	}

	connectWebSocket() {
		const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
		const wsUrl = `${protocol}//${window.location.host}/api/ws/camera`;

		this.websocket = new WebSocket(wsUrl);

		this.websocket.onopen = () => {
			console.log("WebSocket connected");
		};

		this.websocket.onmessage = (event) => {
			const data = JSON.parse(event.data);
			if (data.type === "analysis") {
				this.displayLiveResults(data.data);
			}
		};

		this.websocket.onclose = () => {
			console.log("WebSocket disconnected");
		};

		this.websocket.onerror = (error) => {
			console.error("WebSocket error:", error);
		};
	}

	startLiveAnalysis() {
		this.cameraInterval = setInterval(() => {
			this.captureAndAnalyze();
		}, 5000); // Analyze every 5 seconds
	}

	captureAndAnalyze() {
		if (!this.websocket || this.websocket.readyState !== WebSocket.OPEN) {
			return;
		}

		const video = document.getElementById("cameraVideo");
		const canvas = document.getElementById("cameraCanvas");
		const ctx = canvas.getContext("2d");

		canvas.width = video.videoWidth;
		canvas.height = video.videoHeight;

		ctx.drawImage(video, 0, 0);

		// Convert to base64 and send via WebSocket
		const imageData = canvas.toDataURL("image/jpeg", 0.8);

		this.websocket.send(
			JSON.stringify({
				type: "image",
				image: imageData,
			})
		);
	}

	displayLiveResults(analysisData) {
		const container = document.getElementById("liveResults");

		if (!analysisData.success) {
			container.innerHTML = `
				<div class="live-result-item negative">
					<i class="fas fa-exclamation-triangle me-2"></i>
					Analysis failed: ${analysisData.error}
				</div>
			`;
			return;
		}

		const analysis = analysisData.analysis;
		let resultsHtml = "";

		// Quick rating
		if (analysis.overall_rating || analysis.rating) {
			const rating = analysis.overall_rating || analysis.rating;
			const ratingValue =
				typeof rating === "object"
					? rating.score || rating.value
					: rating;
			const stars = this.generateStars(parseFloat(ratingValue) || 0);

			resultsHtml += `
				<div class="live-result-item positive">
					<h6><i class="fas fa-star me-2"></i>Quick Rating</h6>
					<div class="rating-display">
						<span class="rating-stars">${stars}</span>
						<span class="rating-number">${ratingValue}/10</span>
					</div>
				</div>
			`;
		}

		// Quick feedback
		if (analysis.what_works_well) {
			const strengths = Array.isArray(analysis.what_works_well)
				? analysis.what_works_well.slice(0, 2)
				: [analysis.what_works_well];

			resultsHtml += `
				<div class="live-result-item positive">
					<h6><i class="fas fa-thumbs-up me-2"></i>Strengths</h6>
					<ul class="mb-0">
						${strengths.map((item) => `<li>${item}</li>`).join("")}
					</ul>
				</div>
			`;
		}

		if (analysis.what_could_be_improved || analysis.improvements) {
			const improvements =
				analysis.what_could_be_improved || analysis.improvements;
			const improvementList = Array.isArray(improvements)
				? improvements.slice(0, 2)
				: [improvements];

			resultsHtml += `
				<div class="live-result-item negative">
					<h6><i class="fas fa-lightbulb me-2"></i>Quick Tips</h6>
					<ul class="mb-0">
						${improvementList.map((item) => `<li>${item}</li>`).join("")}
					</ul>
				</div>
			`;
		}

		// Key tip
		if (analysis.key_tip) {
			resultsHtml += `
				<div class="live-result-item positive" style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white;">
					<h6><i class="fas fa-magic me-2"></i>Pro Tip</h6>
					<p class="mb-0">${analysis.key_tip}</p>
				</div>
			`;
		}

		container.innerHTML =
			resultsHtml ||
			`
			<div class="text-center text-muted">
				<i class="fas fa-spinner fa-spin fa-2x mb-3"></i>
				<p>Analyzing...</p>
			</div>
		`;
	}

	showAlert(message, type = "info") {
		// Create alert element
		const alert = document.createElement("div");
		alert.className = `alert alert-${type} alert-dismissible fade show position-fixed`;
		alert.style.cssText =
			"top: 100px; right: 20px; z-index: 9999; min-width: 300px;";
		alert.innerHTML = `
			${message}
			<button type="button" class="btn-close" data-bs-dismiss="alert"></button>
		`;

		document.body.appendChild(alert);

		// Auto remove after 5 seconds
		setTimeout(() => {
			if (alert.parentNode) {
				alert.remove();
			}
		}, 5000);
	}

	async logout() {
		try {
			const response = await fetch("/auth/logout", {
				method: "POST",
				headers: {
					"Content-Type": "application/json",
				},
			});

			if (response.ok) {
				// Redirect to login page
				window.location.href = "/login";
			} else {
				console.error("Logout failed");
			}
		} catch (error) {
			console.error("Logout error:", error);
			// Force redirect anyway
			window.location.href = "/login";
		}
	}
}

// Initialize app when DOM is loaded
document.addEventListener("DOMContentLoaded", () => {
	new FashionCheckApp();
});

// Smooth scroll for anchor links
document.querySelectorAll('a[href^="#"]').forEach((anchor) => {
	anchor.addEventListener("click", function (e) {
		e.preventDefault();
		const target = document.querySelector(this.getAttribute("href"));
		if (target) {
			target.scrollIntoView({
				behavior: "smooth",
				block: "start",
			});
		}
	});
});

// Navbar scroll effect
window.addEventListener("scroll", () => {
	const navbar = document.querySelector(".navbar");
	if (window.scrollY > 50) {
		navbar.classList.add("shadow");
	} else {
		navbar.classList.remove("shadow");
	}
});
