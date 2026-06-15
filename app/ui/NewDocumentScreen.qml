import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

// Screen 2 — New Session form
Rectangle {
    id: root
    focus: true
    color: theme.surfaceBase

    required property var theme
    signal navigate(string screen)
    signal sessionPayloadUpdated(var payload)

    // Form values
    property string documentName: ""
    property string documentTheme: ""
    property string documentGoal: ""
    property string labNumber: ""
    property string sessionHints: ""
    property string lengthMode: ""
    property string imageMode: ""
    property string hasVariants: ""
    property string variantsNumber: ""
    property string templateId: ""
    property string userStyleId: ""

    // Remaining academic field properties
    property string documentTheory: ""
    property string documentTasks: ""
    property string documentQuestions: ""
    property string documentBibliography: ""
    property bool theoryAiCheck: true
    property bool tasksAiCheck: true
    property bool questionsAiCheck: true
    property bool bibliographyAiCheck: true
    property bool includeSpecialInstructions: true
    property bool includeUserStyle: false

    // Uploaded files list
    property var uploadedFiles: []
    property bool showWarning: false
    property string warningText: ""
    property string hoveredFileName: ""
    property string hoveredFileNameDanger: ""

    // Validation modal
    property bool showValidationModal: false
    property string validationMessage: ""

    // Resizable textarea height
    property int hintsHeight: 120

    property bool isRestoring: false
    property bool hasRestored: false
    property var initialPayload: ({})
    property bool hasValidHfToken: true

    function restorePayload(globalPayload) {
        if (hasRestored) return;
        if (globalPayload && Object.keys(globalPayload).length > 0) {
            isRestoring = true;
            var sp = globalPayload;
            root.documentName = sp.documentName || "";
            root.documentTheme = sp.documentTheme || "";
            root.documentGoal = sp.documentGoal || "";
            root.labNumber = sp.labNumber || "";
            root.sessionHints = sp.sessionHints || "";
            root.lengthMode = sp.lengthMode || "";
            root.imageMode = sp.imageMode || "";
            root.hasVariants = sp.hasVariants || "";
            root.variantsNumber = sp.variantsNumber || "";
            root.templateId = sp.template_id || "";
            root.userStyleId = sp.userStyleId || "";
            
            root.documentTheory = sp.documentTheory || "";
            root.documentTasks = sp.documentTasks || "";
            root.documentQuestions = sp.documentQuestions || "";
            root.documentBibliography = sp.documentBibliography || "";
            root.theoryAiCheck = sp.theoryAiCheck !== undefined ? sp.theoryAiCheck : true;
            root.tasksAiCheck = sp.tasksAiCheck !== undefined ? sp.tasksAiCheck : true;
            root.questionsAiCheck = sp.questionsAiCheck !== undefined ? sp.questionsAiCheck : true;
            root.bibliographyAiCheck = sp.bibliographyAiCheck !== undefined ? sp.bibliographyAiCheck : true;
            root.includeSpecialInstructions = sp.includeSpecialInstructions !== undefined ? sp.includeSpecialInstructions : true;
            root.includeUserStyle = sp.includeUserStyle !== undefined ? sp.includeUserStyle : false;
            
            if (typeof nameAiCheck !== "undefined") nameAiCheck.checked = sp.nameAiCheck || false;
            if (typeof themeAiCheck !== "undefined") themeAiCheck.checked = sp.themeAiCheck || false;
            if (typeof goalAiCheck !== "undefined") goalAiCheck.checked = sp.goalAiCheck || false;
            
            if (sp.uploadedFiles && sp.uploadedFiles.length > 0) {
                root.uploadedFiles = sp.uploadedFiles;
                filesModel.clear();
                for (var i = 0; i < sp.uploadedFiles.length; i++) {
                    filesModel.append(sp.uploadedFiles[i]);
                }
            }
            isRestoring = false;
        }
        hasRestored = true;
    }

    Component.onCompleted: {
        hasValidHfToken = (bridge.getHfToken().length === 37)
        if (initialPayload && Object.keys(initialPayload).length > 0) {
            restorePayload(initialPayload);
        } else if (ApplicationWindow.window && ApplicationWindow.window.sessionPayload && Object.keys(ApplicationWindow.window.sessionPayload).length > 0) {
            restorePayload(ApplicationWindow.window.sessionPayload);
        } else {
            restorationTimer.start();
        }
    }

    onInitialPayloadChanged: {
        if (initialPayload && Object.keys(initialPayload).length > 0) {
            restorePayload(initialPayload);
        } else {
            isRestoring = true;
            root.documentName = "";
            root.documentTheme = "";
            root.documentGoal = "";
            root.labNumber = "";
            root.sessionHints = "";
            root.lengthMode = "";
            root.imageMode = "";
            root.hasVariants = "";
            root.variantsNumber = "";
            root.templateId = "";
            root.userStyleId = "";
            root.documentTheory = "";
            root.documentTasks = "";
            root.documentQuestions = "";
            root.documentBibliography = "";
            root.theoryAiCheck = true;
            root.tasksAiCheck = true;
            root.questionsAiCheck = true;
            root.bibliographyAiCheck = true;
            root.includeSpecialInstructions = true;
            root.includeUserStyle = false;
            if (typeof nameAiCheck !== "undefined") nameAiCheck.checked = false;
            if (typeof themeAiCheck !== "undefined") themeAiCheck.checked = false;
            if (typeof goalAiCheck !== "undefined") goalAiCheck.checked = false;
            root.uploadedFiles = [];
            filesModel.clear();
            isRestoring = false;
        }
    }

    Timer {
        id: restorationTimer
        interval: 100
        onTriggered: {
            if (!hasRestored) {
                hasRestored = true;
                saveState(); // Trigger first save
            }
        }
    }

    function saveState() {
        if (!hasRestored || isRestoring) return;
        var globalPayload = initialPayload;
        if ((!globalPayload || Object.keys(globalPayload).length === 0) && ApplicationWindow.window) {
            globalPayload = ApplicationWindow.window.sessionPayload;
        }
        var sp = Object.assign({}, globalPayload || {});
        sp.documentName = root.documentName;
        sp.documentTheme = root.documentTheme;
        sp.documentGoal = root.documentGoal;
        sp.labNumber = root.labNumber;
        sp.sessionHints = root.sessionHints;
        sp.lengthMode = root.lengthMode;
        sp.imageMode = root.imageMode;
        sp.hasVariants = root.hasVariants;
        sp.variantsNumber = root.variantsNumber;
        sp.uploadedFiles = root.uploadedFiles;
        sp.template_id = root.templateId;
        sp.userStyleId = root.userStyleId;
        
        sp.nameAiCheck = nameAiCheck.checked;
        sp.themeAiCheck = themeAiCheck.checked;
        sp.goalAiCheck = goalAiCheck.checked;
        sp.documentTheory = root.documentTheory;
        sp.documentTasks = root.documentTasks;
        sp.documentQuestions = root.documentQuestions;
        sp.documentBibliography = root.documentBibliography;
        sp.theoryAiCheck = root.theoryAiCheck;
        sp.tasksAiCheck = root.tasksAiCheck;
        sp.questionsAiCheck = root.questionsAiCheck;
        sp.bibliographyAiCheck = root.bibliographyAiCheck;
        sp.includeSpecialInstructions = root.includeSpecialInstructions;
        sp.includeUserStyle = root.includeUserStyle;
        
        var isSect1Done = (root.documentName && root.documentName.trim().length >= 5) && (root.labNumber !== "");
        var isSect2Done = (root.documentTheme && root.documentTheme.trim().length > 0) && (root.documentGoal && root.documentGoal.trim().length > 0);
        var isSect3Done = (root.uploadedFiles && root.uploadedFiles.length > 0);
        
        sp.hasCompletedSections = isSect1Done || isSect2Done || isSect3Done;
        
        sessionPayloadUpdated(sp);
    }

    onDocumentNameChanged: saveState()
    onDocumentThemeChanged: saveState()
    onDocumentGoalChanged: saveState()
    onLabNumberChanged: saveState()
    onSessionHintsChanged: saveState()
    onLengthModeChanged: saveState()
    onImageModeChanged: saveState()
    onHasVariantsChanged: saveState()
    onVariantsNumberChanged: saveState()
    onUploadedFilesChanged: saveState()
    onDocumentTheoryChanged: saveState()
    onDocumentTasksChanged: saveState()
    onDocumentQuestionsChanged: saveState()
    onDocumentBibliographyChanged: saveState()
    onTheoryAiCheckChanged: saveState()
    onTasksAiCheckChanged: saveState()
    onQuestionsAiCheckChanged: saveState()
    onBibliographyAiCheckChanged: saveState()
    onIncludeSpecialInstructionsChanged: saveState()
    onIncludeUserStyleChanged: saveState()

    onTemplateIdChanged: {
        if (templateCombo && templateCombo.model) {
            var idx = -1;
            for (var i = 0; i < templateCombo.model.length; i++) {
                if (templateCombo.model[i].id === root.templateId) { idx = i; break; }
            }
            templateCombo.currentIndex = idx;
        }
        saveState();
    }



    ListModel { id: filesModel }

    Connections {
        target: bridge
        function onFilesUpdated(jsonStr) {
            var newFiles = JSON.parse(jsonStr)
            
            // Sync filesModel
            for (var i = filesModel.count - 1; i >= 0; i--) {
                var found = false;
                for (var j = 0; j < newFiles.length; j++) {
                    if (filesModel.get(i).name === newFiles[j].name) { found = true; break; }
                }
                if (!found) filesModel.remove(i);
            }
            var appended = false;
            for (var k = 0; k < newFiles.length; k++) {
                var exists = false;
                for (var m = 0; m < filesModel.count; m++) {
                    if (filesModel.get(m).name === newFiles[k].name) {
                        filesModel.setProperty(m, "status", newFiles[k].status);
                        filesModel.setProperty(m, "symbols", newFiles[k].symbols);
                        exists = true;
                        break;
                    }
                }
                if (!exists) {
                    filesModel.append(newFiles[k]);
                    appended = true;
                }
            }

            root.uploadedFiles = newFiles
            if (appended) {
                autoScrollTimer.restart()
            }
        }
        function onFileWarning(msg) {
            root.warningText = msg
            root.showWarning = true
            warningTimer.restart()
        }
    }

    Timer {
        id: autoScrollTimer
        interval: 50
        onTriggered: {
            if (!smoothScrollAnim.running && !scrollView.dragging && !scrollView.moving) {
                var newY = Math.max(0, scrollView.contentHeight - scrollView.height);
                if (newY > scrollView.contentY) {
                    smoothScrollAnim.to = newY;
                    smoothScrollAnim.restart();
                }
            }
        }
    }

    Timer {
        id: warningTimer
        interval: 10000
        onTriggered: root.showWarning = false
    }

    NumberAnimation {
        id: smoothScrollAnim
        target: scrollView
        property: "contentY"
        duration: 400
        easing.type: Easing.OutCubic
    }

    // ── Scrollable content area (leaves room for sticky footer) ───────────────
    Flickable {
        id: scrollView
        visible: hasValidHfToken
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        // Stop exactly at the footer so the scrollbar doesn't go behind it
        anchors.bottom: stickyFooter.top
        contentWidth: width
        contentHeight: formCol.implicitHeight + 64
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        interactive: false
        WheelHandler {
            orientation: Qt.Vertical
            onWheel: (event) => {
                var speedMultiplier = 0.5;
                if (event.pixelDelta.y !== 0) {
                    smoothScrollAnim.stop();
                    var delta = event.pixelDelta.y * speedMultiplier;
                    scrollView.contentY = Math.max(0, Math.min(scrollView.contentHeight - scrollView.height, scrollView.contentY - delta));
                } else {
                    var current = smoothScrollAnim.running ? smoothScrollAnim.to : scrollView.contentY;
                    var step = event.angleDelta.y * speedMultiplier;
                    var newY = Math.max(0, Math.min(scrollView.contentHeight - scrollView.height, current - step));
                    if (newY !== current) {
                        smoothScrollAnim.to = newY;
                        smoothScrollAnim.restart();
                    }
                }
            }
        }
        ScrollBar.vertical: ScrollBar { id: vbar; policy: ScrollBar.AsNeeded }

        Item {
            width: scrollView.width
            height: scrollView.contentHeight

            ColumnLayout {
                id: formCol
                    anchors {
                        top: parent.top; topMargin: theme.sp8
                        horizontalCenter: parent.horizontalCenter
                    }
                    width: Math.min(parent.width - theme.sp10 * 2, 720)
                    spacing: theme.sp4

                    // Page title
                    Text {
                        text: "Новий документ"
                        font.pixelSize: theme.fontSizeH1
                        font.weight: Font.DemiBold
                        color: theme.textPrimary
                        font.letterSpacing: -0.3
                        Layout.alignment: Qt.AlignHCenter
                        horizontalAlignment: Text.AlignHCenter
                    }
                    Text {
                        textFormat: Text.RichText
                        text: "Заповніть обов'язкові поля (<font color='#EF4444'>*</font>).<br>ШІ згенерує документ за обраним шаблоном."
                        font.pixelSize: theme.fontSizeLG
                        color: theme.textSecondary
                        Layout.bottomMargin: theme.sp4
                        Layout.alignment: Qt.AlignHCenter
                        horizontalAlignment: Text.AlignHCenter
                    }



                    // ── Session Name (Top level) ──────────────────────────────
                    Rectangle {
                        id: nameBlock
                        property bool isDone: root.documentName && root.documentName.trim().length >= 5
                        Layout.fillWidth: true
                        radius: theme.radiusLG; color: theme.surface1
                        border.color: isDone ? theme.accent : theme.borderSubtle
                        border.width: isDone ? 2 : 1
                        Behavior on border.color { ColorAnimation { duration: 400; easing.type: Easing.OutCubic } }
                        Behavior on border.width { NumberAnimation { duration: 400; easing.type: Easing.OutCubic } }
                        Layout.preferredHeight: nameCol.implicitHeight + theme.sp6 * 2
                        
                        ColumnLayout {
                            id: nameCol
                            anchors { left: parent.left; right: parent.right; top: parent.top; margins: theme.sp6 }
                            spacing: theme.sp4

                            RowLayout {
                                spacing: theme.sp3
                                Rectangle {
                                    width: 28; height: 28; radius: 14
                                    color: nameBlock.isDone ? theme.accent : theme.accentSoft2
                                    Behavior on color { ColorAnimation { duration: 300 } }
                                    Text {
                                        anchors.centerIn: parent; text: "1"
                                        font.pixelSize: theme.fontSizeMD; font.weight: Font.DemiBold; color: theme.accent
                                        opacity: nameBlock.isDone ? 0 : 1
                                        scale: nameBlock.isDone ? 0.5 : 1
                                        Behavior on opacity { NumberAnimation { duration: 300; easing.type: Easing.OutBack } }
                                        Behavior on scale { NumberAnimation { duration: 300; easing.type: Easing.OutBack } }
                                    }
                                    Text {
                                        anchors.centerIn: parent
                                        text: "✓"
                                        font.pixelSize: theme.fontSizeMD
                                        font.weight: Font.DemiBold
                                        color: "white"
                                        opacity: nameBlock.isDone ? 1 : 0
                                        scale: nameBlock.isDone ? 1 : 0.5
                                        Behavior on opacity { NumberAnimation { duration: 300; easing.type: Easing.OutBack } }
                                        Behavior on scale { NumberAnimation { duration: 300; easing.type: Easing.OutBack } }
                                    }
                                }
                                RowLayout {
                                    spacing: 2
                                    Text { text: "Назва документу"; font.pixelSize: theme.fontSizeXL; font.weight: Font.DemiBold; color: theme.textPrimary }
                                    Text { text: " *"; font.pixelSize: theme.fontSizeXL; font.weight: Font.DemiBold; color: "#EF4444" }
                                }
                            }
                            TextField {
                                id: nameField; Layout.fillWidth: true
                                placeholderText: "Наприклад: Лабораторна робота №1 — сортування масивів"
                                font.pixelSize: theme.fontSizeLG
                                text: root.documentName
                                onTextChanged: root.documentName = text
                            }
                            CheckBox {
                                id: nameAiCheck
                                text: "Дозволити ШІ покращити це поле ✨"
                                font.pixelSize: theme.fontSizeSM
                                checked: false
                                onCheckedChanged: saveState()
                            }
                        }
                    }

                    // ── Section 1: Template & Style ────────────────────────────
                    Rectangle {
                        id: block1
                        property bool isDone: (root.templateId !== "")
                        Layout.fillWidth: true
                        radius: theme.radiusLG; color: theme.surface1
                        border.color: isDone ? theme.accent : theme.borderSubtle
                        border.width: isDone ? 2 : 1
                        Behavior on border.color { ColorAnimation { duration: 400; easing.type: Easing.OutCubic } }
                        Behavior on border.width { NumberAnimation { duration: 400; easing.type: Easing.OutCubic } }
                        Layout.preferredHeight: sec1Col.implicitHeight + theme.sp6 * 2

                        ColumnLayout {
                            id: sec1Col
                            anchors { left: parent.left; right: parent.right; top: parent.top; margins: theme.sp6 }
                            spacing: theme.sp4

                            RowLayout {
                                spacing: theme.sp3
                                Rectangle {
                                    width: 28; height: 28; radius: 14
                                    color: block1.isDone ? theme.accent : theme.accentSoft2
                                    Behavior on color { ColorAnimation { duration: 300 } }
                                    Text {
                                        anchors.centerIn: parent; text: "2"
                                        font.pixelSize: theme.fontSizeMD; font.weight: Font.DemiBold; color: theme.accent
                                        opacity: block1.isDone ? 0 : 1
                                        scale: block1.isDone ? 0.5 : 1
                                        Behavior on opacity { NumberAnimation { duration: 300; easing.type: Easing.OutBack } }
                                        Behavior on scale { NumberAnimation { duration: 300; easing.type: Easing.OutBack } }
                                    }
                                    Text {
                                        anchors.centerIn: parent; text: "✓"
                                        font.pixelSize: theme.fontSizeMD; font.weight: Font.DemiBold; color: "white"
                                        opacity: block1.isDone ? 1 : 0
                                        scale: block1.isDone ? 1 : 0.5
                                        Behavior on opacity { NumberAnimation { duration: 300; easing.type: Easing.OutBack } }
                                        Behavior on scale { NumberAnimation { duration: 300; easing.type: Easing.OutBack } }
                                    }
                                }
                                Text { text: "Налаштування шаблону"; font.pixelSize: theme.fontSizeXL; font.weight: Font.DemiBold; color: theme.textPrimary }
                            }

                            // Template Block
                            RowLayout {
                                spacing: 2
                                Text { text: "Шаблон документа"; font.pixelSize: theme.fontSizeMD; font.weight: Font.Medium; color: theme.textPrimary }
                                Text { text: " *"; font.pixelSize: theme.fontSizeMD; font.weight: Font.Medium; color: "#EF4444" }
                            }
                            ComboBox {
                                id: templateCombo
                                Layout.fillWidth: true
                                font.pixelSize: theme.fontSizeMD
                                model: bridge ? JSON.parse(bridge.getTemplates()) : []
                                textRole: "display_name"
                                valueRole: "id"
                                currentIndex: -1
                                displayText: currentIndex === -1 ? "Виберіть зі списку" : currentText
                                Component.onCompleted: {
                                    var idx = -1;
                                    for (var i = 0; i < model.length; i++) {
                                        if (model[i].id === root.templateId) { idx = i; break; }
                                    }
                                    currentIndex = idx;
                                }
                                onActivated: {
                                    root.templateId = currentValue
                                    saveState()
                                }
                            }

                            CheckBox {
                                id: specialInstructionsCheck
                                text: "Враховувати спеціальні інструкції шаблону " + (root.templateId ? "(" + root.templateId + "_fill.md)" : "") + " 📋"
                                font.pixelSize: theme.fontSizeSM
                                checked: root.includeSpecialInstructions
                                onToggled: root.includeSpecialInstructions = checked
                            }

                            CheckBox {
                                id: userStyleCheck
                                text: "Враховувати користувацький стиль (user_style.md) 🎨"
                                font.pixelSize: theme.fontSizeSM
                                checked: root.includeUserStyle
                                onToggled: root.includeUserStyle = checked
                            }
                        }
                    }

                    // ── Section 1: Basic Info ──────────────────────────────────
                    Rectangle {
                        id: block2
                        property bool isDone: (root.documentTheme ? root.documentTheme.trim().length >= 5 : false) && 
                                              (root.labNumber ? root.labNumber.trim() !== "" : false)
                        Layout.fillWidth: true
                        radius: theme.radiusLG; color: theme.surface1
                        border.color: isDone ? theme.accent : theme.borderSubtle
                        border.width: isDone ? 2 : 1
                        Behavior on border.color { ColorAnimation { duration: 400; easing.type: Easing.OutCubic } }
                        Behavior on border.width { NumberAnimation { duration: 400; easing.type: Easing.OutCubic } }
                        Layout.preferredHeight: sec2Col.implicitHeight + theme.sp6 * 2

                        ColumnLayout {
                            id: sec2Col
                            anchors { left: parent.left; right: parent.right; top: parent.top; margins: theme.sp6 }
                            spacing: theme.sp4

                            RowLayout {
                                spacing: theme.sp3
                                Rectangle {
                                    width: 28; height: 28; radius: 14
                                    color: block2.isDone ? theme.accent : theme.accentSoft2
                                    Behavior on color { ColorAnimation { duration: 300 } }
                                    Text {
                                        anchors.centerIn: parent; text: "3"
                                        font.pixelSize: theme.fontSizeMD; font.weight: Font.DemiBold; color: theme.accent
                                        opacity: block2.isDone ? 0 : 1
                                        scale: block2.isDone ? 0.5 : 1
                                        Behavior on opacity { NumberAnimation { duration: 300; easing.type: Easing.OutBack } }
                                        Behavior on scale { NumberAnimation { duration: 300; easing.type: Easing.OutBack } }
                                    }
                                    Text {
                                        anchors.centerIn: parent; text: "✓"
                                        font.pixelSize: theme.fontSizeMD; font.weight: Font.DemiBold; color: "white"
                                        opacity: block2.isDone ? 1 : 0
                                        scale: block2.isDone ? 1 : 0.5
                                        Behavior on opacity { NumberAnimation { duration: 300; easing.type: Easing.OutBack } }
                                        Behavior on scale { NumberAnimation { duration: 300; easing.type: Easing.OutBack } }
                                    }
                                }
                                Text { text: "Основні дані"; font.pixelSize: theme.fontSizeXL; font.weight: Font.DemiBold; color: theme.textPrimary }
                            }
                            Text {
                                text: "Не менше 5 символів в текстових полях"
                                font.pixelSize: theme.fontSizeSM
                                color: theme.textSecondary
                                Layout.bottomMargin: theme.sp2
                            }

                            // Номер лабораторної (moved to top)
                            RowLayout {
                                spacing: 2
                                Text { text: "Номер лабораторної"; font.pixelSize: theme.fontSizeMD; font.weight: Font.Medium; color: theme.textPrimary }
                                Text { text: " *"; font.pixelSize: theme.fontSizeMD; font.weight: Font.Medium; color: "#EF4444" }
                            }
                            TextField {
                                id: labNumField
                                Layout.preferredWidth: Math.max(200, implicitWidth + 20)
                                placeholderText: "Наприклад: 7"
                                font.pixelSize: theme.fontSizeLG
                                inputMethodHints: Qt.ImhDigitsOnly
                                validator: RegularExpressionValidator { regularExpression: /^[1-9][0-9]?$/ }
                                text: root.labNumber
                                onTextChanged: root.labNumber = text
                            }

                            // Тема *
                            RowLayout {
                                spacing: 2
                                Text { text: "Тема"; font.pixelSize: theme.fontSizeMD; font.weight: Font.Medium; color: theme.textPrimary }
                                Text { text: " *"; font.pixelSize: theme.fontSizeMD; font.weight: Font.Medium; color: "#EF4444" }
                            }
                            TextField {
                                id: themeField
                                Layout.fillWidth: true
                                placeholderText: "Наприклад: Порівняння алгоритмів сортування"
                                font.pixelSize: theme.fontSizeLG
                                validator: RegularExpressionValidator { regularExpression: /^[^\d]*$/ }
                                text: root.documentTheme
                                onTextChanged: root.documentTheme = text
                            }
                            CheckBox {
                                id: themeAiCheck
                                text: "Дозволити ШІ покращити це поле ✨"
                                font.pixelSize: theme.fontSizeSM
                                checked: false
                                onCheckedChanged: saveState()
                            }

                            // Мета
                            Text { text: "Мета"; font.pixelSize: theme.fontSizeMD; font.weight: Font.Medium; color: theme.textPrimary }
                            TextArea {
                                id: goalField
                                Layout.fillWidth: true
                                placeholderText: "Опишіть, що має бути в документі..."
                                height: 80; font.pixelSize: theme.fontSizeLG
                                wrapMode: TextArea.Wrap
                                text: root.documentGoal
                                property bool wasEmpty: true
                                onTextChanged: {
                                    if (/\d/.test(text)) {
                                        var oldPos = cursorPosition
                                        var newText = text.replace(/\d/g, "")
                                        var diff = text.length - newText.length
                                        text = newText
                                        cursorPosition = Math.max(0, oldPos - diff)
                                    }
                                    
                                    var isEmpty = (text.trim() === "")
                                    if (goalField.activeFocus) {
                                        if (wasEmpty && !isEmpty) {
                                            goalAiCheck.checked = false
                                        } else if (!wasEmpty && isEmpty) {
                                            goalAiCheck.checked = true
                                        }
                                    }
                                    wasEmpty = isEmpty
                                    
                                    root.documentGoal = text
                                }
                            }
                            // AI checkbox for Мета — locked when field is empty
                            ColumnLayout {
                                spacing: 4
                                CheckBox {
                                    id: goalAiCheck
                                    text: "Дозволити ШІ покращити це поле ✨"
                                    font.pixelSize: theme.fontSizeSM
                                    checked: true
                                    // Cannot uncheck when Мета is empty
                                    enabled: goalField.text.trim() !== ""
                                    opacity: enabled ? 1.0 : 0.7
                                    onCheckedChanged: saveState()
                                }
                                Text {
                                    visible: goalAiCheck.checked && goalField.text.trim() === ""
                                    font.pixelSize: theme.fontSizeSM
                                    color: theme.textSecondary
                                    textFormat: Text.RichText
                                    text: "<b>Примітка:</b> Якщо Мету не введено — ШІ має її написати"
                                    wrapMode: Text.Wrap
                                    Layout.fillWidth: true
                                }
                            }

                            // Теоретичні відомості
                            Text { text: "Теоретичні відомості"; font.pixelSize: theme.fontSizeMD; font.weight: Font.Medium; color: theme.textPrimary }
                            TextArea {
                                id: theoryField
                                Layout.fillWidth: true
                                placeholderText: "Введіть теоретичні відомості (якщо порожньо — ШІ згенерує за темою)..."
                                height: 80; font.pixelSize: theme.fontSizeLG
                                wrapMode: TextArea.Wrap
                                text: root.documentTheory
                                onTextChanged: root.documentTheory = text
                            }
                            CheckBox {
                                id: theoryAiCheck
                                text: "Дозволити ШІ покращити це поле ✨"
                                font.pixelSize: theme.fontSizeSM
                                checked: root.theoryAiCheck
                                onToggled: root.theoryAiCheck = checked
                            }

                            // Завдання
                            Text { text: "Завдання (по одному на рядок)"; font.pixelSize: theme.fontSizeMD; font.weight: Font.Medium; color: theme.textPrimary }
                            TextArea {
                                id: tasksField
                                Layout.fillWidth: true
                                placeholderText: "Введіть завдання, кожне на новому рядку..."
                                height: 80; font.pixelSize: theme.fontSizeLG
                                wrapMode: TextArea.Wrap
                                text: root.documentTasks
                                onTextChanged: root.documentTasks = text
                            }
                            CheckBox {
                                id: tasksAiCheck
                                text: "Дозволити ШІ покращити це поле ✨"
                                font.pixelSize: theme.fontSizeSM
                                checked: root.tasksAiCheck
                                onToggled: root.tasksAiCheck = checked
                            }

                            // Контрольні запитання
                            Text { text: "Контрольні запитання (по одному на рядок)"; font.pixelSize: theme.fontSizeMD; font.weight: Font.Medium; color: theme.textPrimary }
                            TextArea {
                                id: questionsField
                                Layout.fillWidth: true
                                placeholderText: "Введіть контрольні запитання, кожне на новому рядку..."
                                height: 80; font.pixelSize: theme.fontSizeLG
                                wrapMode: TextArea.Wrap
                                text: root.documentQuestions
                                onTextChanged: root.documentQuestions = text
                            }
                            CheckBox {
                                id: questionsAiCheck
                                text: "Дозволити ШІ покращити це поле ✨"
                                font.pixelSize: theme.fontSizeSM
                                checked: root.questionsAiCheck
                                onToggled: root.questionsAiCheck = checked
                            }

                            // Література
                            Text { text: "Література (по одному джерелу на рядок)"; font.pixelSize: theme.fontSizeMD; font.weight: Font.Medium; color: theme.textPrimary }
                            TextArea {
                                id: bibliographyField
                                Layout.fillWidth: true
                                placeholderText: "Введіть джерела літератури, кожне на новому рядку..."
                                height: 80; font.pixelSize: theme.fontSizeLG
                                wrapMode: TextArea.Wrap
                                text: root.documentBibliography
                                onTextChanged: root.documentBibliography = text
                            }
                            CheckBox {
                                id: bibliographyAiCheck
                                text: "Дозволити ШІ покращити це поле ✨"
                                font.pixelSize: theme.fontSizeSM
                                checked: root.bibliographyAiCheck
                                onToggled: root.bibliographyAiCheck = checked
                            }
                        }
                    }

                    // ── Section 3: Parameters ──────────────────────────────────
                    Rectangle {
                        id: sec3Rect
                        property bool isDone: {
                            var isLenDone = (root.lengthMode !== "");
                            var isVarDone = true;
                            if (root.templateId === "lab2") {
                                isVarDone = (root.hasVariants === "no" || (root.hasVariants === "yes" && parseInt(root.variantsNumber, 10) >= 2));
                            }
                            var isHintsOk = (root.sessionHints.trim() === "" || root.sessionHints.trim().length >= 5);
                            return isLenDone && isVarDone && isHintsOk;
                        }
                        Layout.fillWidth: true
                        radius: theme.radiusLG; color: theme.surface1
                        border.color: isDone ? theme.accent : theme.borderSubtle
                        border.width: isDone ? 2 : 1
                        Behavior on border.color { ColorAnimation { duration: 400; easing.type: Easing.OutCubic } }
                        Behavior on border.width { NumberAnimation { duration: 400; easing.type: Easing.OutCubic } }
                        Layout.preferredHeight: sec3Col.implicitHeight + theme.sp6 * 2

                        ColumnLayout {
                            id: sec3Col
                            anchors { left: parent.left; right: parent.right; top: parent.top; margins: theme.sp6 }
                            spacing: theme.sp5

                            RowLayout {
                                spacing: theme.sp3
                                Rectangle {
                                    width: 28; height: 28; radius: 14
                                    color: sec3Rect.isDone ? theme.accent : theme.accentSoft2
                                    Behavior on color { ColorAnimation { duration: 300 } }
                                    Text {
                                        anchors.centerIn: parent; text: "4"
                                        font.pixelSize: theme.fontSizeMD; font.weight: Font.DemiBold; color: theme.accent
                                        opacity: sec3Rect.isDone ? 0 : 1
                                        scale: sec3Rect.isDone ? 0.5 : 1
                                        Behavior on opacity { NumberAnimation { duration: 300; easing.type: Easing.OutBack } }
                                        Behavior on scale { NumberAnimation { duration: 300; easing.type: Easing.OutBack } }
                                    }
                                    Text {
                                        anchors.centerIn: parent; text: "✓"
                                        font.pixelSize: theme.fontSizeMD; font.weight: Font.DemiBold; color: "white"
                                        opacity: sec3Rect.isDone ? 1 : 0
                                        scale: sec3Rect.isDone ? 1 : 0.5
                                        Behavior on opacity { NumberAnimation { duration: 300; easing.type: Easing.OutBack } }
                                        Behavior on scale { NumberAnimation { duration: 300; easing.type: Easing.OutBack } }
                                    }
                                }
                                Text { text: "Параметри генерації"; font.pixelSize: theme.fontSizeXL; font.weight: Font.DemiBold; color: theme.textPrimary }
                            }

                            RowLayout {
                                spacing: 2
                                Text { text: "Обсяг документа"; font.pixelSize: theme.fontSizeMD; font.weight: Font.Medium; color: theme.textPrimary }
                                Text { text: " *"; font.pixelSize: theme.fontSizeMD; font.weight: Font.Medium; color: "#EF4444" }
                            }
                            // Inline flow of pills (allows wrapping)
                            Flow {
                                Layout.fillWidth: true
                                spacing: theme.sp2
                                Repeater {
                                    model: [
                                        {id:"short",  label:"Короткий (500–1000)"},
                                        {id:"middle", label:"Середній (1000–1700)"},
                                        {id:"long",   label:"Довгий (1700–2500)"},
                                        {id:"large",  label:"Великий (2500+)"}
                                    ]
                                    delegate: PillButton {
                                        theme: root.theme
                                        label: modelData.label
                                        active: root.lengthMode === modelData.id
                                        onClicked: {
                                            root.lengthMode = (root.lengthMode === modelData.id) ? "" : modelData.id
                                            root.forceActiveFocus() // Prevent stealing focus
                                            saveState()
                                        }
                                    }
                                }
                            }

                            RowLayout {
                                spacing: 2
                                Text { text: "Режим зображень"; font.pixelSize: theme.fontSizeMD; font.weight: Font.Medium; color: theme.textPrimary }
                            }
                            Flow {
                                Layout.fillWidth: true
                                spacing: theme.sp2
                                Repeater {
                                    model: [
                                        {id:"none",       label:"Вимкнути зображення"},
                                        {id:"full",       label:"Увімкнути зображення"}
                                    ]
                                    delegate: PillButton {
                                        theme: root.theme
                                        label: modelData.label
                                        active: root.imageMode === modelData.id
                                        onClicked: {
                                            root.imageMode = (root.imageMode === modelData.id) ? "" : modelData.id
                                            root.forceActiveFocus()
                                            saveState()
                                        }
                                    }
                                }
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: theme.sp4
                                visible: root.templateId === "lab2"

                                RowLayout {
                                    spacing: 2
                                    Text { text: "Додати індивідуальні завдання по варіантам?"; font.pixelSize: theme.fontSizeMD; font.weight: Font.Medium; color: theme.textPrimary }
                                    Text { text: " *"; font.pixelSize: theme.fontSizeMD; font.weight: Font.Medium; color: "#EF4444" }
                                }
                                Flow {
                                    Layout.fillWidth: true
                                    spacing: theme.sp2
                                    PillButton {
                                        theme: root.theme
                                        label: "Так"
                                        active: root.hasVariants === "yes"
                                        onClicked: {
                                            root.hasVariants = (root.hasVariants === "yes") ? "" : "yes"
                                            root.forceActiveFocus() // Prevent stealing focus
                                            saveState()
                                        }
                                    }
                                    PillButton {
                                        theme: root.theme
                                        label: "Ні"
                                        active: root.hasVariants === "no"
                                        onClicked: {
                                            if (root.hasVariants === "no") {
                                                root.hasVariants = ""
                                            } else {
                                                root.hasVariants = "no"
                                                root.variantsNumber = ""
                                            }
                                            root.forceActiveFocus()
                                            saveState()
                                        }
                                    }
                                }
                                
                                RowLayout {
                                    visible: opacity > 0
                                    opacity: root.hasVariants === "yes" ? 1 : 0
                                    Behavior on opacity { NumberAnimation { duration: 250; easing.type: Easing.OutCubic } }
                                    spacing: theme.sp3
                                    RowLayout {
                                        spacing: 2
                                        Text { text: "Кількість варіантів:"; font.pixelSize: theme.fontSizeMD; color: theme.textSecondary }
                                        Text { text: " *"; font.pixelSize: theme.fontSizeMD; color: "#EF4444" }
                                    }
                                    TextField {
                                        placeholderText: "Наприклад: 10"
                                        font.pixelSize: theme.fontSizeMD
                                        Layout.preferredWidth: Math.max(150, implicitWidth + 20)
                                        validator: RegularExpressionValidator { regularExpression: /^[1-9][0-9]?$/ }
                                        onTextChanged: root.variantsNumber = text
                                        text: root.variantsNumber
                                    }
                                }
                            }
                            
                            Text { text: "Додаткові вказівки"; font.pixelSize: theme.fontSizeMD; font.weight: Font.Medium; color: theme.textPrimary }
                            TextArea {
                                id: hintsArea
                                Layout.fillWidth: true
                                Layout.preferredHeight: root.hintsHeight
                                placeholderText: "ШІ має врахувати наступне..."
                                font.pixelSize: theme.fontSizeLG
                                wrapMode: TextArea.Wrap
                                topPadding: 12
                                bottomPadding: 24
                                leftPadding: 12
                                rightPadding: 12
                                text: root.sessionHints
                                onTextChanged: root.sessionHints = text
                                
                                MouseArea {
                                    id: resizeGripHints
                                    hoverEnabled: true
                                    height: 16
                                    anchors.bottom: parent.bottom
                                    anchors.left: parent.left
                                    anchors.right: parent.right
                                    cursorShape: Qt.SizeVerCursor
                                    
                                    property int startY: 0
                                    onPressed: function(mouse) { startY = mapToItem(root, mouse.x, mouse.y).y }
                                    onPositionChanged: function(mouse) {
                                        if (!pressed) return;
                                        var currentY = mapToItem(root, mouse.x, mouse.y).y
                                        var dy = currentY - startY
                                        startY = currentY
                                        root.hintsHeight = Math.max(120, Math.min(root.hintsHeight + dy, 1200))
                                        
                                        // Auto-scroll logic
                                        var mappedY = mapToItem(scrollView, mouse.x, mouse.y).y
                                        if (mappedY > scrollView.height - 40) {
                                            scrollView.contentY = Math.min(scrollView.contentY + 15, scrollView.contentHeight - scrollView.height)
                                        } else if (mappedY < 40) {
                                            scrollView.contentY = Math.max(0, scrollView.contentY - 15)
                                        }
                                    }

                                    Row {
                                        anchors.centerIn: parent
                                        spacing: 4
                                        Repeater {
                                            model: 3
                                            Rectangle {
                                                width: 4; height: 4; radius: 2
                                                color: resizeGripHints.containsMouse || resizeGripHints.pressed ? theme.accentHover : theme.accent
                                                Behavior on color { ColorAnimation { duration: 150 } }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }

                    // ── Section 4: Files ───────────────────────────────────────
                    Rectangle {
                        id: sec4Rect
                        visible: typeof bridge !== "undefined" && bridge !== null && bridge.supportAttachFiles !== undefined ? bridge.supportAttachFiles : true
                        property bool isDone: {
                            if (!visible) return true;
                            if (!root.uploadedFiles || root.uploadedFiles.length === 0) return false;
                            for (var i = 0; i < root.uploadedFiles.length; i++) {
                                if (root.uploadedFiles[i].status !== "done") return false;
                            }
                            return contextBarCol.pct < 100;
                        }
                        Layout.fillWidth: true
                        radius: theme.radiusLG; color: theme.surface1
                        border.color: isDone ? theme.accent : theme.borderSubtle
                        border.width: isDone ? 2 : 1
                        Behavior on border.color { ColorAnimation { duration: 400; easing.type: Easing.OutCubic } }
                        Behavior on border.width { NumberAnimation { duration: 400; easing.type: Easing.OutCubic } }
                        Layout.preferredHeight: visible ? (sec4Col.implicitHeight + theme.sp6 * 2) : 0

                        ColumnLayout {
                            id: sec4Col
                            anchors { left: parent.left; right: parent.right; top: parent.top; margins: theme.sp6 }
                            spacing: theme.sp3

                            RowLayout {
                                spacing: theme.sp3
                                Rectangle {
                                    width: 28; height: 28; radius: 14
                                    color: sec4Rect.isDone ? theme.accent : theme.accentSoft2
                                    Behavior on color { ColorAnimation { duration: 300 } }
                                    Text {
                                        anchors.centerIn: parent; text: "5"
                                        font.pixelSize: theme.fontSizeMD; font.weight: Font.DemiBold; color: theme.accent
                                        opacity: sec4Rect.isDone ? 0 : 1
                                        scale: sec4Rect.isDone ? 0.5 : 1
                                        Behavior on opacity { NumberAnimation { duration: 300; easing.type: Easing.OutBack } }
                                        Behavior on scale { NumberAnimation { duration: 300; easing.type: Easing.OutBack } }
                                    }
                                    Text {
                                        anchors.centerIn: parent; text: "✓"
                                        font.pixelSize: theme.fontSizeMD; font.weight: Font.DemiBold; color: "white"
                                        opacity: sec4Rect.isDone ? 1 : 0
                                        scale: sec4Rect.isDone ? 1 : 0.5
                                        Behavior on opacity { NumberAnimation { duration: 300; easing.type: Easing.OutBack } }
                                        Behavior on scale { NumberAnimation { duration: 300; easing.type: Easing.OutBack } }
                                    }
                                }
                                Text { text: "Файли для контексту"; font.pixelSize: theme.fontSizeXL; font.weight: Font.DemiBold; color: theme.textPrimary }
                            }

                            // Drop zone
                            Rectangle {
                                id: dropZone
                                focus: true
                                Layout.fillWidth: true; Layout.preferredHeight: Math.max(150, dropCol.implicitHeight + theme.sp4 * 2); radius: theme.radiusLG
                                color: dropZone.dropHover ? theme.accentSoft2 : theme.surface2
                                Behavior on color { ColorAnimation { duration: 100 } }
                                property bool dropHover: false

                                Rectangle {
                                    anchors.fill: parent
                                    color: "transparent"
                                    border.color: dropZone.dropHover ? theme.accent : theme.borderStrong
                                    border.width: 2
                                    radius: theme.radiusLG
                                    Behavior on border.color { ColorAnimation { duration: 100 } }
                                }

                                ColumnLayout {
                                    id: dropCol
                                    anchors.centerIn: parent; spacing: theme.sp1
                                    Text { Layout.alignment: Qt.AlignHCenter; text: "📎"; font.pixelSize: 28 }
                                    Text { Layout.alignment: Qt.AlignHCenter; text: "Перетягніть файли сюди"; font.pixelSize: theme.fontSizeLG; font.weight: Font.Medium; color: theme.textPrimary }
                                    Text { Layout.alignment: Qt.AlignHCenter; text: "або"; font.pixelSize: theme.fontSizeMD; color: theme.textSecondary }
                                    Text { Layout.alignment: Qt.AlignHCenter; text: "Натисніть сюди щоб обрати файли"; font.pixelSize: theme.fontSizeLG; font.weight: Font.Medium; color: theme.textPrimary }
                                    Text { Layout.alignment: Qt.AlignHCenter; text: "PDF, DOCX, PPTX, PNG, JPG (< 50 МБ)"; font.pixelSize: theme.fontSizeSM; color: theme.textTertiary }
                                }

                                MouseArea {
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    acceptedButtons: Qt.NoButton
                                    cursorShape: Qt.PointingHandCursor
                                    onEntered: dropZone.dropHover = true
                                    onExited: dropZone.dropHover = false
                                }
                                
                                TapHandler {
                                    onTapped: bridge.openFileDialog()
                                }

                                DropArea {
                                    anchors.fill: parent
                                    onEntered: (drag) => {
                                        dropZone.dropHover = true
                                        drag.accepted = true
                                    }
                                    onExited: dropZone.dropHover = false
                                    onDropped: (drop) => {
                                        dropZone.dropHover = false
                                        if (drop.hasUrls) {
                                            var urlStrings = []
                                            for (var i = 0; i < drop.urls.length; i++) {
                                                urlStrings.push(drop.urls[i].toString())
                                            }
                                            bridge.uploadFilesUrls(urlStrings)
                                        }
                                    }
                                }
                            }

                            // Context Capacity Bar
                            ColumnLayout {
                                id: contextBarCol
                                Layout.fillWidth: true
                                spacing: theme.sp2
                                visible: root.uploadedFiles.length > 0

                                property int totalSymbols: {
                                    var sum = 0;
                                    for (var i = 0; i < root.uploadedFiles.length; i++) {
                                        if (root.uploadedFiles[i].status === "done") {
                                            sum += root.uploadedFiles[i].symbols || 0;
                                        }
                                    }
                                    return sum;
                                }
                                property real pct: Math.min(totalSymbols / 150000 * 100, 100)
                                property bool wasFull: false
                                onPctChanged: {
                                    if (pct >= 100 && !wasFull) {
                                        pulseAnim.start()
                                        wasFull = true
                                    } else if (pct < 100) {
                                        wasFull = false
                                    }
                                }

                                Text {
                                    text: "Заповненість контекстного вікна"
                                    font.pixelSize: theme.fontSizeLG
                                    font.weight: Font.DemiBold
                                    color: theme.textPrimary
                                    Layout.alignment: Qt.AlignHCenter
                                    Layout.fillWidth: true
                                    Layout.topMargin: theme.sp4
                                    horizontalAlignment: Text.AlignHCenter
                                }
                                Text {
                                    text: "(Наведіть курсор щоб побачити подробиці)"
                                    font.pixelSize: theme.fontSizeMD
                                    color: theme.textSecondary
                                    Layout.alignment: Qt.AlignHCenter
                                    Layout.fillWidth: true
                                    horizontalAlignment: Text.AlignHCenter
                                    Layout.bottomMargin: theme.sp1
                                }

                                Item { // The progress bar row container
                                    id: progressBarContainer
                                    Layout.fillWidth: true
                                    Layout.topMargin: theme.sp2
                                    Layout.bottomMargin: theme.sp1
                                    height: 32
                                    scale: 1.0

                                    SequentialAnimation {
                                        id: pulseAnim
                                        NumberAnimation { target: progressBarContainer; property: "scale"; to: 1.03; duration: 150; easing.type: Easing.OutQuad }
                                        NumberAnimation { target: progressBarContainer; property: "scale"; to: 1.0; duration: 150; easing.type: Easing.InQuad }
                                        NumberAnimation { target: progressBarContainer; property: "scale"; to: 1.015; duration: 100; easing.type: Easing.OutQuad }
                                        NumberAnimation { target: progressBarContainer; property: "scale"; to: 1.0; duration: 100; easing.type: Easing.InQuad }
                                    }

                                    property real actualWidth: Math.min(contextBarCol.pct / 100 * width, width)
                                    Behavior on actualWidth { NumberAnimation { duration: 400; easing.type: Easing.OutCubic } }

                                    Rectangle {
                                        anchors.fill: parent
                                        radius: 0
                                        color: theme.surface2
                                        border.color: contextBarCol.pct >= 95 ? theme.textPrimary : theme.borderSubtle
                                        border.width: contextBarCol.pct >= 95 ? 2 : 1
                                        clip: true

                                        Item {
                                            anchors.fill: parent
                                            anchors.margins: parent.border.width
                                            clip: true
                                            Row {
                                                anchors.fill: parent
                                            Repeater {
                                                model: root.uploadedFiles
                                                Rectangle {
                                                    id: segmentRect
                                                    property int sym: modelData.status === "done" ? (modelData.symbols || 0) : 0
                                                    visible: modelData.status === "done"
                                                    property string fName: modelData.name || ""
                                                    property real scaleDivisor: 150000
                                                    property real calculatedWidth: (sym / scaleDivisor) * parent.width
                                                    width: Math.max(4, calculatedWidth)
                                                    Behavior on width { NumberAnimation { duration: 400; easing.type: Easing.OutCubic } }

                                                    height: parent.height
                                                    color: root.hoveredFileNameDanger === fName ? theme.danger : (contextBarCol.pct >= 100 ? theme.danger : (contextBarCol.pct >= 80 ? theme.warning : theme.accent))
                                                    Behavior on color { ColorAnimation { duration: 400 } }
                                                    border.color: theme.surfaceBase
                                                    border.width: 1
                                                    opacity: root.hoveredFileName === "" || root.hoveredFileName === fName ? 1.0 : 0.3
                                                    Behavior on opacity { NumberAnimation { duration: 150; easing.type: Easing.OutSine } }
                                                    z: root.hoveredFileName === fName ? 10 : 1

                                                    // Calculate the visible width inside the Row so the ToolTip can center correctly
                                                    property real visibleWidth: Math.max(0, Math.min(width, parent.width - x))

                                                    MouseArea {
                                                        id: segMouse
                                                        anchors.fill: parent
                                                        hoverEnabled: true
                                                        onEntered: root.hoveredFileName = fName
                                                        onExited: { if (root.hoveredFileName === fName) root.hoveredFileName = "" }

                                                        ToolTip {
                                                            x: segmentRect.visibleWidth / 2 - width / 2
                                                            y: -height - 6
                                                            visible: root.hoveredFileName === fName
                                                            contentItem: Item {
                                                                implicitWidth: txt.implicitWidth
                                                                implicitHeight: txt.implicitHeight + 8
                                                                Text {
                                                                    id: txt
                                                                    anchors.top: parent.top
                                                                    anchors.horizontalCenter: parent.horizontalCenter
                                                                    text: fName + "\n" + (sym / 150000 * 100).toFixed(1) + "% від ліміту • " + (modelData.meta || "")
                                                                    color: "white"
                                                                    font.pixelSize: 13
                                                                    horizontalAlignment: Text.AlignHCenter
                                                                }
                                                            }
                                                            background: Item {
                                                                Rectangle {
                                                                    anchors.fill: parent
                                                                    anchors.bottomMargin: 6
                                                                    color: "#2C2C2C"
                                                                    radius: 6
                                                                    border.color: "#404040"
                                                                    border.width: 1
                                                                }
                                                                Rectangle {
                                                                    width: 12
                                                                    height: 12
                                                                    color: "#2C2C2C"
                                                                    border.color: "#404040"
                                                                    border.width: 1
                                                                    rotation: 45
                                                                    anchors.bottom: parent.bottom
                                                                    anchors.bottomMargin: 0
                                                                    anchors.horizontalCenter: parent.horizontalCenter
                                                                    z: -1
                                                                }
                                                                Rectangle {
                                                                    width: 14
                                                                    height: 8
                                                                    color: "#2C2C2C"
                                                                    anchors.bottom: parent.bottom
                                                                    anchors.bottomMargin: 6
                                                                    anchors.horizontalCenter: parent.horizontalCenter
                                                                }
                                                            }
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                    }

                                    // Blinking cursor
                                    Rectangle {
                                        x: parent.actualWidth - 2
                                        y: -2
                                        width: 4
                                        height: parent.height + 4
                                        color: theme.textPrimary
                                        radius: 2
                                        visible: contextBarCol.pct < 95
                                        SequentialAnimation on color {
                                            loops: Animation.Infinite
                                            ColorAnimation { to: "gray"; duration: 500 }
                                            ColorAnimation { to: "white"; duration: 500 }
                                        }
                                    }

                                    // Text next to cursor
                                    Text {
                                        x: Math.min(parent.actualWidth + 10, parent.width - width - 5)
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: contextBarCol.pct.toFixed(1) + "%"
                                        font.pixelSize: theme.fontSizeSM
                                        color: theme.textSecondary
                                        font.weight: Font.Medium
                                        visible: contextBarCol.pct < 95
                                    }
                                }

                                RowLayout {
                                    Layout.fillWidth: true
                                    Item { Layout.fillWidth: true } // Spacer
                                    Text {
                                        text: contextBarCol.totalSymbols + " / 150000 символів" + (contextBarCol.pct >= 95 ? " (" + contextBarCol.pct.toFixed(1) + "%)" : "")
                                        font.pixelSize: theme.fontSizeSM
                                        color: contextBarCol.pct >= 100 ? theme.danger : (contextBarCol.pct >= 80 ? theme.warning : theme.textSecondary)
                                        font.weight: Font.Medium
                                        Behavior on color { ColorAnimation { duration: 400 } }
                                    }
                                }
                            }

                            // Dynamic file rows
                            Repeater {
                                model: filesModel
                                delegate: Item {
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: fileRow.height
                                    width: parent.width
                                    height: fileRow.height
                                    
                                    opacity: root.hoveredFileName !== "" && root.hoveredFileName !== name ? 0.3 : 1.0
                                    Behavior on opacity { NumberAnimation { duration: 200 } }
                                    
                                    Component.onCompleted: {
                                        fileRow.opacity = 0;
                                        fileRow.y = -10;
                                        anim.start();
                                    }
                                    ParallelAnimation {
                                        id: anim
                                        NumberAnimation { target: fileRow; property: "opacity"; to: 1; duration: 300 }
                                        NumberAnimation { target: fileRow; property: "y"; to: 0; duration: 300; easing.type: Easing.OutBack }
                                    }

                                    FileRow {
                                        id: fileRow
                                        width: parent.width
                                        theme: root.theme
                                        fileName: name
                                        fileMeta: typeof meta !== 'undefined' ? meta : ""
                                        fileStatus: typeof status !== 'undefined' ? status : "processing"
                                        fileSymbols: typeof symbols !== 'undefined' ? symbols : 0
                                        isHovered: root.hoveredFileName === name
                                        onHoverStateChanged: (hovering) => {
                                            if (hovering) root.hoveredFileName = name
                                            else if (root.hoveredFileName === name) root.hoveredFileName = ""
                                        }
                                        onDangerHoverStateChanged: (hovering) => {
                                            if (hovering) {
                                                root.hoveredFileNameDanger = name
                                            } else {
                                                if (root.hoveredFileNameDanger === name) root.hoveredFileNameDanger = ""
                                            }
                                        }
                                        onDeleteRequested: {
                                            delAnim.start();
                                        }

                                        SequentialAnimation {
                                            id: delAnim
                                            ParallelAnimation {
                                                NumberAnimation { target: fileRow; property: "opacity"; to: 0; duration: 250; easing.type: Easing.OutCubic }
                                                NumberAnimation { target: fileRow; property: "height"; to: 0; duration: 250; easing.type: Easing.InOutQuad }
                                            }
                                            ScriptAction { script: bridge.deleteFile(name) }
                                        }
                                    }
                                }
                            }

                            // Duplicate/warning toast
                            Rectangle {
                                visible: root.showWarning
                                Layout.fillWidth: true
                                Layout.preferredHeight: warnText.implicitHeight + 16
                                radius: theme.radiusMD
                                color: theme.warningSoft
                                border.color: theme.warning; border.width: 1
                                Text {
                                    id: warnText
                                    anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: 12 }
                                    text: root.warningText
                                    font.pixelSize: theme.fontSizeSM
                                    color: theme.warning
                                    wrapMode: Text.Wrap
                                }
                            }

                        }
                    }
                    // Bottom spacer
                    Item { height: 24 }
                }
            }
        }

    // ── Sticky Footer ──────────────────────────────────────────────────────────
    Rectangle {
        id: stickyFooter
        visible: hasValidHfToken
        anchors.bottom: parent.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        height: 64
        color: theme.surface1
        z: 10

        Rectangle {
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.right: parent.right
            height: 1
            color: theme.borderSubtle
        }

        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: theme.sp4
            anchors.rightMargin: theme.sp4

            AppButton {
                theme: root.theme
                label: "Скасувати"
                variant: "secondary"
                onClicked: root.navigate("documents")
            }

            Item { Layout.fillWidth: true }

            AppButton {
                theme: root.theme
                label: "✦  Створити"
                variant: "primary"
                onClicked: {
                    var errors = []
                    
                    if (root.templateId === undefined || root.templateId === "") {
                        errors.push("• Шаблон документа: обов'язково оберіть зі списку")
                    }
                    if (root.documentName === undefined || root.documentName.trim().length < 5) {
                        errors.push("• Назва документу: мінімум 5 символів")
                    }
                    if (root.labNumber === undefined || root.labNumber.trim() === "") {
                        errors.push("• Номер лабораторної: обов'язкове поле")
                    }
                    if (root.documentTheme === undefined || root.documentTheme.trim().length < 5) {
                        errors.push("• Тема: мінімум 5 символів")
                    }
                    if (root.documentGoal !== undefined && root.documentGoal.trim().length > 0 && root.documentGoal.trim().length < 5) {
                        errors.push("• Мета: якщо введено, мінімум 5 символів")
                    }
                    if (root.documentTheory !== undefined && root.documentTheory.trim().length > 0 && root.documentTheory.trim().length < 5) {
                        errors.push("• Теоретичні відомості: якщо введено, мінімум 5 символів")
                    }
                    if (root.documentTasks !== undefined && root.documentTasks.trim().length > 0 && root.documentTasks.trim().length < 5) {
                        errors.push("• Завдання: якщо введено, мінімум 5 символів")
                    }
                    if (root.documentQuestions !== undefined && root.documentQuestions.trim().length > 0 && root.documentQuestions.trim().length < 5) {
                        errors.push("• Контрольні запитання: якщо введено, мінімум 5 символів")
                    }
                    if (root.documentBibliography !== undefined && root.documentBibliography.trim().length > 0 && root.documentBibliography.trim().length < 5) {
                        errors.push("• Література: якщо введено, мінімум 5 символів")
                    }
                    if (root.lengthMode === "") {
                        errors.push("• Обсяг документа: обов'язково оберіть один із варіантів")
                    }
                    if (root.templateId === "lab2") {
                        if (root.hasVariants === "") {
                            errors.push("• Додати індивідуальні завдання: обов'язково оберіть Так або Ні")
                        } else if (root.hasVariants === "yes") {
                            var vNum = parseInt(root.variantsNumber, 10);
                            if (isNaN(vNum) || vNum < 2) {
                                errors.push("• Кількість варіантів: мінімум 2")
                            }
                        }
                    }
                    if (root.sessionHints.trim().length > 0 && root.sessionHints.trim().length < 5) {
                        errors.push("• Додаткові вказівки: мінімум 5 символів")
                    }

                    if (bridge.supportAttachFiles) {
                        var hasFileError = false;
                        var hasProcessing = false;
                        for (var i = 0; i < root.uploadedFiles.length; i++) {
                            if (root.uploadedFiles[i].status === "error") {
                                hasFileError = true;
                            } else if (root.uploadedFiles[i].status === "processing") {
                                hasProcessing = true;
                            }
                        }
                        if (hasProcessing) {
                            errors.push("• Зачекайте завершення завантаження всіх файлів")
                        }
                        if (hasFileError) {
                            errors.push("• Видаліть файли з помилками, щоб продовжити")
                        }
                        if (contextBarCol.pct >= 100) {
                            errors.push("• Заповненість контексту перевищує 100%. Видаліть зайві файли.")
                        }
                    }
                    
                    if (errors.length > 0) {
                        if (errors.length === 1) {
                            root.validationMessage = "Помилка заповнення форми:\n\n" + errors[0]
                        } else {
                            root.validationMessage = "Кілька полів заповнені неправильно. Вимоги:\n\n" + errors.join("\n")
                        }
                        validationPopup.open()
                        return
                    }

                    ApplicationWindow.window.sessionPayload = {
                        "documentName": root.documentName,
                        "template_id": root.templateId,
                        "userStyleId": root.userStyleId,
                        "lengthMode": root.lengthMode,
                        "image_mode": root.imageMode,
                        "hasVariants": root.templateId === "lab2" ? root.hasVariants : "no",
                        "variantsNumber": root.templateId === "lab2" ? root.variantsNumber : "",
                        "documentTheme": root.documentTheme,
                        "documentGoal": root.documentGoal,
                        "documentTheory": root.documentTheory,
                        "documentTasks": root.documentTasks,
                        "documentQuestions": root.documentQuestions,
                        "documentBibliography": root.documentBibliography,
                        "labNumber": root.labNumber,
                        "sessionHints": root.sessionHints,
                        "uploadedFiles": bridge.supportAttachFiles ? root.uploadedFiles : [],
                        "nameAiCheck": nameAiCheck.checked,
                        "themeAiCheck": themeAiCheck.checked,
                        "goalAiCheck": goalAiCheck.checked,
                        "theoryAiCheck": root.theoryAiCheck,
                        "tasksAiCheck": root.tasksAiCheck,
                        "questionsAiCheck": root.questionsAiCheck,
                        "bibliographyAiCheck": root.bibliographyAiCheck,
                        "includeSpecialInstructions": root.includeSpecialInstructions,
                        "includeUserStyle": root.includeUserStyle,
                        "hasCompletedSections": true
                    }
                    root.navigate("confirm_document")
                }
            }
        }
    }

    // ── Missing Token Warning ───────────────────────────────────────────────
    Item {
        anchors.fill: parent
        visible: !hasValidHfToken

        ColumnLayout {
            anchors.centerIn: parent
            spacing: theme.sp4

            Text {
                Layout.alignment: Qt.AlignHCenter
                text: "Відсутній або некоректний Hugging Face API ключ"
                font.pixelSize: theme.fontSizeH1
                font.weight: Font.DemiBold
                color: theme.textPrimary
            }

            Text {
                Layout.alignment: Qt.AlignHCenter
                text: "Для створення документа потрібен валідний ключ доступу з 37 символів."
                font.pixelSize: theme.fontSizeMD
                color: theme.textSecondary
            }

            Item { Layout.preferredHeight: theme.sp4 }

            AppButton {
                Layout.alignment: Qt.AlignHCenter
                theme: root.theme
                label: "Як це зробити? Подивитись інструкцію"
                variant: "primary"
                onClicked: {
                    root.navigate("settings_instructions")
                }
            }
        }
    }

    // ── Validation modal ───────────────────────────────────────────────────────
    Popup {
        id: validationPopup
        width: Math.min(380, parent.width - 40)
        height: modalCol.implicitHeight + theme.sp8 * 2
        parent: Overlay.overlay
        anchors.centerIn: parent
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        Overlay.modal: Rectangle {
            color: "#80000000"
        }
        background: Rectangle {
            radius: theme.radiusXL; color: theme.surface1
            border.color: theme.borderSubtle; border.width: 1
        }

        contentItem: Item {
            ColumnLayout {
                id: modalCol
                anchors { left: parent.left; right: parent.right; top: parent.top; margins: theme.sp8 }
                spacing: theme.sp5

                // Icon + title
                RowLayout {
                    spacing: theme.sp3
                    Text { 
                        text: "⚠️"
                        font.pixelSize: 24
                        Layout.alignment: Qt.AlignVCenter
                    }
                    Text {
                        text: "Не всі поля заповнені"
                        font.pixelSize: 20
                        font.weight: Font.DemiBold
                        color: theme.textPrimary
                        Layout.alignment: Qt.AlignVCenter
                    }
                }

                Text {
                    text: root.validationMessage
                    font.pixelSize: theme.fontSizeMD
                    color: theme.textSecondary
                    wrapMode: Text.Wrap
                    Layout.fillWidth: true
                }

                // OK button
                AppButton {
                    Layout.alignment: Qt.AlignRight
                    theme: root.theme
                    label: "Зрозуміло"
                    variant: "primary"
                    onClicked: validationPopup.close()
                }
            }
        }
    }


}
