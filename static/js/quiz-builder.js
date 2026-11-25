(() => {
  const count = document.getElementById('id_question_count');
  const questions = document.getElementById('quiz-questions');
  if (!count || !questions) return;
  const prototype = questions.querySelector('fieldset').cloneNode(true);
  const update = () => {
    if (!count.reportValidity()) return;
    const total = Number(count.value);
    if (!Number.isInteger(total) || total < 1 || total > 100) return;
    while (questions.children.length < total) {
      const row = prototype.cloneNode(true);
      const index = questions.children.length + 1;
      row.querySelector('legend').textContent = `Question ${index}`;
      row.querySelectorAll('.errorlist').forEach(error => error.remove());
      row.querySelectorAll('[name], [id], label[for]').forEach(element => {
        ['name', 'id', 'for'].forEach(attribute => {
          if (element.hasAttribute(attribute)) {
            element.setAttribute(attribute, element.getAttribute(attribute).replace(/question_\d+_/g, `question_${index}_`));
          }
        });
      });
      row.querySelectorAll('input, textarea, select').forEach(field => {
        if (field.tagName === 'SELECT') field.selectedIndex = 0;
        else field.value = field.name.endsWith('_marks') ? '1' : '';
      });
      questions.appendChild(row);
    }
    [...questions.children].forEach((row, index) => {
      row.hidden = index >= total;
      row.disabled = index >= total;
    });
    document.getElementById('question-count-status').textContent = `${total} questions selected. Hidden questions will not be saved.`;
  };
  document.getElementById('apply-question-count').addEventListener('click', update);
  count.addEventListener('change', update);
})();
