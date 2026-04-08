class CreatePromptRequests < ActiveRecord::Migration[8.1]
  def change
    create_table :prompt_requests do |t|
      t.integer :status, null: false, default: 0
      t.text :prompt_text, null: false
      t.text :response_text
      t.text :error_message
      t.text :metadata_json

      t.string :lease_token
      t.datetime :lease_expires_at
      t.integer :attempts, null: false, default: 0
      t.string :result_idempotency_key

      t.datetime :claimed_at
      t.datetime :completed_at
      t.datetime :failed_at

      t.timestamps
    end

    add_index :prompt_requests, :status
    add_index :prompt_requests, :lease_expires_at
    add_index :prompt_requests, :lease_token
  end
end