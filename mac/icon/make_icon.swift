// Crop the rounded square out of the source art and render a macOS-style icon (1024x1024, transparent corners).
import Cocoa
let src = NSImage(contentsOfFile: "source.webp")!
let rep = NSBitmapImageRep(data: src.tiffRepresentation!)!
let crop = CGRect(x: 167, y: 167, width: 690, height: 675)        // measured rounded-square bounds (top-left origin)
let cg = rep.cgImage!.cropping(to: crop)!
let size = 1024, body: CGFloat = 824, inset = (CGFloat(size) - body) / 2, radius: CGFloat = 185
let ctx = CGContext(data: nil, width: size, height: size, bitsPerComponent: 8, bytesPerRow: 0,
                    space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
ctx.interpolationQuality = .high
let rect = CGRect(x: inset, y: inset, width: body, height: body)
// soft shadow like other Mac icons
ctx.setShadow(offset: CGSize(width: 0, height: -10), blur: 24, color: CGColor(gray: 0, alpha: 0.35))
ctx.addPath(CGPath(roundedRect: rect, cornerWidth: radius, cornerHeight: radius, transform: nil))
ctx.setFillColor(CGColor(red: 0.1, green: 0.16, blue: 0.25, alpha: 1)); ctx.fillPath()
ctx.setShadow(offset: .zero, blur: 0, color: nil)
ctx.addPath(CGPath(roundedRect: rect, cornerWidth: radius, cornerHeight: radius, transform: nil)); ctx.clip()
ctx.draw(cg, in: rect)
let out = NSBitmapImageRep(cgImage: ctx.makeImage()!)
try! out.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: "icon_1024.png"))
print("icon_1024.png written")
