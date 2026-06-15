import QtQuick 2.15
import QtQuick.Layouts 1.15

// Pill radio group
Flow {
    id: root
    spacing: theme.sp2

    required property var theme
    required property var options   // [{id, label}]
    property string selected: ""

    signal selectionChanged(string value)

    Repeater {
        model: root.options
        delegate: PillButton {
            theme: root.theme
            label: modelData.label
            active: root.selected === modelData.id
            onClicked: {
                root.selected = modelData.id
                root.selectionChanged(modelData.id)
            }
        }
    }
}
