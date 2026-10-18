// FieldModel calls these during gestures; a widget has no gestures and no haptics, so they do nothing here.
enum Haptics {
    static func tap(_ intensity: Double = 0.6) {}
    static func select() {}
}
