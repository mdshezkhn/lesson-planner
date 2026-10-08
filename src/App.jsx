import { useState } from 'react'
import './App.css'

function App() {
  const [selectedCategories, setSelectedCategories] = useState(['Reading']);
  const [interactive, setInteractive] = useState(true);

  const categories = ['Grammar', 'Reading', 'Speaking', 'Writing', 'Phonics', 'Science'];

  const toggleCategory = (cat) => {
    if (selectedCategories.includes(cat)) {
      setSelectedCategories(selectedCategories.filter(c => c !== cat));
    } else {
      setSelectedCategories([...selectedCategories, cat]);
    }
  };

  const handleGenerate = async () => {
    try {
      // Show loading indicator in a real app
      const formData = new FormData();
      formData.append("gradeLevel", "5th Grade"); // You could add state for this
      formData.append("categories", selectedCategories.join(", "));
      formData.append("objectives", "Learn main ideas"); // You could add state for this
      formData.append("interactive", interactive);

      const host = window.location.hostname;
      const response = await fetch(`http://${host}:8000/generate-presentation`, {
        method: "POST",
        body: formData,
      });

      if (!response.ok) throw new Error("Failed to generate");

      // Download the file returned by the backend
      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "AI_Lesson_Plan.pptx";
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      a.remove();
      
    } catch (error) {
      console.error(error);
      alert("There was an error generating the presentation.");
    }
  };

  return (
    <div className="glass-panel app-container">
      <h2 className="title">Teacher Mo's ESL/EAL lesson presentation maker</h2>
      
      <div className="form-group">
        <label className="form-label">
          <span>📄</span> Upload
        </label>
        <div className="upload-zone">
          <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" /></svg>
          <p className="upload-title">Drag & Drop or Click to Upload</p>
          <p className="upload-subtitle">Previous Year Presentation/Worksheets</p>
          <p className="upload-subtitle">(supports PDF, PPTX, DOCX)</p>
        </div>
      </div>

      <div className="form-group">
        <label className="form-label">
          <span>🎓</span> Grade Level
        </label>
        <select className="form-select">
          <option>Kindergarten</option>
          <option>1st Grade</option>
          <option>2nd Grade</option>
          <option>3rd Grade</option>
          <option>4th Grade</option>
          <option>5th Grade</option>
          <option>6th Grade</option>
        </select>
      </div>

      <div className="form-group">
        <label className="form-label">
          <span>🏷️</span> Category
        </label>
        <div className="categories-container">
          {categories.map(cat => (
            <div 
              key={cat} 
              className={`category-pill ${selectedCategories.includes(cat) ? 'selected' : ''}`}
              onClick={() => toggleCategory(cat)}
            >
              {cat}
            </div>
          ))}
        </div>
      </div>

      <div className="form-group">
        <label className="form-label">
          <span>🎯</span> New Lesson Objectives
        </label>
        <textarea 
          className="form-textarea" 
          placeholder="Describe your main objectives for the new lesson...&#10;e.g., Learn to identify and use main ideas and supporting details in a text."
        ></textarea>
      </div>

      <div className="toggle-group">
        <span className="toggle-label">Include Games & Interactive Activities 🎮</span>
        <label className="switch">
          <input 
            type="checkbox" 
            checked={interactive} 
            onChange={(e) => setInteractive(e.target.checked)} 
          />
          <span className="slider"></span>
        </label>
      </div>

      <button className="submit-btn" onClick={handleGenerate}>
        Generate Presentation (.pptx)
      </button>
    </div>
  )
}

export default App
